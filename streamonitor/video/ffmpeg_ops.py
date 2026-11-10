"""Cut and join recordings with ffmpeg, without re-encoding.

Everything here uses stream copy (`-c copy`), so a 10 GB file is cut in seconds
and not a single bit of quality is lost. Two consequences the callers must know
about:

  * Cuts land on the nearest keyframe, so pieces are not exactly N minutes.
    Being frame-accurate would mean re-encoding: minutes or hours per file,
    quality loss, and CPU stolen from the live recordings.
  * Joining only works when every input shares codec, resolution and audio
    layout. That is not a safe assumption here: models rotate the camera
    mid-show, so one recording can swap between 1080x1440 and 1920x1080.
    `check_joinable` exists to catch that before ffmpeg fails with a raw error.
"""
import json
import os
import re
import subprocess

from parameters import FFMPEG_PATH, CONTAINER

FFPROBE_PATH = os.environ.get('STRMNTR_FFPROBE_PATH') or (
    os.path.join(os.path.dirname(FFMPEG_PATH), 'ffprobe')
    if os.path.dirname(FFMPEG_PATH) else 'ffprobe')

VIDEO_EXTS = ('mp4', 'mkv', 'webm', 'mov', 'avi', 'wmv', 'ts')


class FFmpegError(Exception):
    pass


def _run(cmd, timeout=14400):
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def probe(path):
    """Codec/resolution/duration of a file, or None if unreadable."""
    code, out, _ = _run([
        FFPROBE_PATH, '-v', 'error', '-print_format', 'json',
        '-show_format', '-show_streams', path], timeout=300)
    if code != 0 or not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None

    video = audio = None
    for stream in data.get('streams', []):
        kind = stream.get('codec_type')
        if kind == 'video' and video is None:
            video = stream
        elif kind == 'audio' and audio is None:
            audio = stream
    if video is None:
        return None

    try:
        duration = float(data.get('format', {}).get('duration') or 0)
    except ValueError:
        duration = 0.0

    return {
        'path': path,
        'name': os.path.basename(path),
        'size': int(data.get('format', {}).get('size') or os.path.getsize(path)),
        'duration': duration,
        'video_codec': video.get('codec_name'),
        'width': int(video.get('width') or 0),
        'height': int(video.get('height') or 0),
        'audio_codec': audio.get('codec_name') if audio else None,
        'sample_rate': audio.get('sample_rate') if audio else None,
        'channels': audio.get('channels') if audio else None,
    }


def signature(info):
    """What must match for a stream-copy join to be possible."""
    return (info['video_codec'], info['width'], info['height'],
            info['audio_codec'], info['sample_rate'], info['channels'])


def describe(info):
    audio = f"{info['audio_codec']} {info['sample_rate']}Hz" if info['audio_codec'] else 'sin audio'
    return f"{info['video_codec']} {info['width']}x{info['height']} / {audio}"


def check_joinable(paths):
    """-> (ok, groups, problems)

    `groups` maps a signature to the files carrying it. More than one group
    means a stream-copy join is impossible; the caller should offer the
    compatible subsets instead of letting ffmpeg fail.
    """
    groups, problems = {}, []
    for path in paths:
        info = probe(path)
        if info is None:
            problems.append(f"{os.path.basename(path)}: no se puede leer o no tiene vídeo")
            continue
        groups.setdefault(signature(info), []).append(info)
    return (len(groups) == 1 and not problems), groups, problems


def _unique(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f'{stem}-{n}{ext}'):
        n += 1
    return f'{stem}-{n}{ext}'


def free_bytes(path):
    import shutil
    target = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    return shutil.disk_usage(target).free


def cut(path, minutes, outdir=None, progress=None):
    """Split `path` into pieces of about `minutes` each. Returns the new files.

    The pieces together take as much room as the original, so the caller is
    expected to have checked free space first (see require_space).
    """
    if minutes <= 0:
        raise FFmpegError('Los minutos de corte deben ser mayores que 0')
    info = probe(path)
    if info is None:
        raise FFmpegError('No se puede leer el vídeo de origen')

    outdir = outdir or os.path.dirname(os.path.abspath(path))
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    ext = CONTAINER
    pattern = os.path.join(outdir, f'{stem}-parte%03d.{ext}')

    existing = set(os.listdir(outdir))
    if progress:
        progress(f'Cortando {info["name"]} en bloques de {minutes} min')

    code, _, err = _run([
        FFMPEG_PATH, '-nostdin', '-v', 'error', '-fflags', '+ignidx',
        '-i', path, '-c', 'copy', '-map', '0',
        '-f', 'segment', '-segment_time', str(int(minutes * 60)),
        '-reset_timestamps', '1', '-y', pattern])

    produced = sorted(
        os.path.join(outdir, f) for f in os.listdir(outdir)
        if f not in existing and re.match(rf'^{re.escape(stem)}-parte\d{{3}}\.{ext}$', f))

    if not produced:
        raise FFmpegError(f'ffmpeg no produjo ningún trozo: {err.strip()[:300]}')
    # A non-zero exit with output present means a damaged tail, not a failure:
    # everything up to the damage is in the pieces already.
    return produced


def join(paths, output, progress=None):
    """Concatenate `paths` into `output` by stream copy.

    Refuses mixed formats rather than producing a broken file.
    """
    if len(paths) < 2:
        raise FFmpegError('Hacen falta al menos 2 vídeos para unir')

    ok, groups, problems = check_joinable(paths)
    if problems:
        raise FFmpegError('; '.join(problems))
    if not ok:
        detail = ' | '.join(
            f'{describe(files[0])}: {len(files)} fichero(s)' for files in groups.values())
        raise FFmpegError(
            'Los vídeos no son compatibles para unir sin recodificar porque '
            f'cambian de formato ({detail}). Une por separado los de cada formato.')

    output = _unique(output)
    listfile = output + '.concat.txt'
    with open(listfile, 'w') as f:
        for p in paths:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))

    if progress:
        progress(f'Uniendo {len(paths)} vídeos')
    try:
        code, _, err = _run([
            FFMPEG_PATH, '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '0',
            '-i', listfile, '-c', 'copy', '-y', output])
    finally:
        if os.path.exists(listfile):
            os.remove(listfile)

    if not os.path.exists(output) or os.path.getsize(output) < 1000:
        raise FFmpegError(f'ffmpeg no pudo unir: {err.strip()[:300]}')
    result = probe(output)
    if result is None or result['duration'] < 1:
        raise FFmpegError('El fichero unido no es válido')
    return output, result
