"""Recover fMP4 recordings that ffmpeg's mov demuxer refuses to read.

Despite the .tmp.ts name these files are fragmented MP4: StripChat serves
fMP4/CMAF segments and hls.py concatenates them. Two things make a plain
`ffmpeg -i file -c copy out.mp4` fail, and both leave the recording stranded:

  * the concatenated per-segment indexes (sidx) confuse the demuxer, which
    builds a full sample index up front and aborts -> `-fflags +ignidx`;
  * one damaged fragment anywhere in the file aborts the whole read, even
    though everything after it is intact -> resume past it.

A file can also hold SEVERAL independent streams back to back, because hls.py
rewrites the init segment whenever the HLS stream restarts. ffmpeg reads the
first and stops.

Nothing here re-encodes: every byte of media is copied verbatim.
"""
import os
import struct
import subprocess

import streamonitor.log as log
from parameters import FFMPEG_PATH

logger = log.Logger('fmp4_repair')

# Index/metadata boxes. Dropping them is what makes a concatenation readable.
DROP = {b'sidx', b'styp', b'mfra', b'free', b'skip'}
FFPROBE_PATH = (os.path.join(os.path.dirname(FFMPEG_PATH), 'ffprobe')
                if os.path.dirname(FFMPEG_PATH) else 'ffprobe')


def _boxes(path):
    total = os.path.getsize(path)
    out, off = [], 0
    with open(path, 'rb') as f:
        while off < total:
            f.seek(off)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size = struct.unpack('>I', hdr[:4])[0]
            typ = hdr[4:8]
            if size == 1:
                size = struct.unpack('>Q', f.read(8))[0]
            elif size == 0:
                size = total - off
            if size < 8 or off + size > total:
                break          # truncated tail: stop at the last whole box
            out.append((off, size, typ))
            off += size
    return out


def _streams(path):
    """-> [(init_boxes, [(moof, mdat), ...]), ...], one entry per stream."""
    result, init, frags, pending = [], [], [], None
    for off, size, typ in _boxes(path):
        if typ in DROP:
            continue
        if typ == b'ftyp':
            if init and frags:
                result.append((init, frags))
            init, frags, pending = [(off, size)], [], None
        elif typ == b'moov':
            init.append((off, size))
        elif typ == b'moof':
            pending = (off, size)
        elif typ == b'mdat' and pending:
            frags.append((pending, (off, size)))
            pending = None
    if init and frags:
        result.append((init, frags))
    return result


def _write_slice(src, dst, init, frags):
    with open(src, 'rb') as f, open(dst, 'wb') as o:
        for off, size in init:
            f.seek(off)
            o.write(f.read(size))
        for (mo, ms), (do, ds) in frags:
            f.seek(mo)
            o.write(f.read(ms))
            f.seek(do)
            left = ds
            while left:
                chunk = f.read(min(4 << 20, left))
                if not chunk:
                    break
                o.write(chunk)
                left -= len(chunk)


def probe_duration(path):
    try:
        out = subprocess.run(
            [FFPROBE_PATH, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', path],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300).stdout
        return float(out.strip().split('\n')[0])
    except Exception:
        return 0.0


def remux(src, dst, extra_input_args=('-fflags', '+ignidx')):
    """Stream-copy remux. ffmpeg exits non-zero on a damaged tail even when it
    wrote everything up to the damage, so success is judged on the output."""
    cmd = [FFMPEG_PATH, '-nostdin', '-v', 'error']
    cmd.extend(extra_input_args)
    cmd.extend(['-i', src, '-c', 'copy', '-map', '0', '-f', 'mp4', '-y', dst])
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=14400)
    except Exception as e:
        logger.warning(f'ffmpeg falló sobre {os.path.basename(src)}: {e}')
    if not os.path.exists(dst) or os.path.getsize(dst) < 1_000_000:
        if os.path.exists(dst):
            os.remove(dst)
        return 0.0
    duration = probe_duration(dst)
    if duration < 1:
        os.remove(dst)
        return 0.0
    return duration


def repair(src, out_base, workdir, min_seconds=2.0):
    """Recover `src` into one or more mp4 files named from `out_base`.

    Returns the list of files produced (possibly empty). Several files come
    back when the recording holds segments that cannot be concatenated by
    copy, e.g. the model rotated the camera and the resolution changed.
    """
    os.makedirs(workdir, exist_ok=True)
    parts = []
    try:
        all_streams = _streams(src)
    except Exception as e:
        logger.error(f'No se pudo analizar {os.path.basename(src)}: {e}')
        return []

    for si, (init, frags) in enumerate(all_streams):
        start = 0
        while start < len(frags):
            slice_path = os.path.join(workdir, f'slice_{si}_{start}.mp4')
            part_path = os.path.join(workdir, f'part_{len(parts):03d}.mp4')
            try:
                _write_slice(src, slice_path, init, frags[start:])
                duration = remux(slice_path, part_path)
            except Exception as e:
                logger.warning(f'Fragmento {si}/{start} ilegible: {e}')
                duration = 0.0
            finally:
                if os.path.exists(slice_path):
                    os.remove(slice_path)

            if duration < min_seconds:
                if os.path.exists(part_path):
                    os.remove(part_path)
                start += 1                       # this fragment is the bad one
                continue

            # Count how many input fragments fit in what came out, then skip
            # one more: that next fragment is what stopped the demuxer.
            budget = os.path.getsize(part_path) * 1.02
            used = acc = 0
            for (_, _), (_, ds) in frags[start:]:
                acc += ds
                if acc > budget:
                    break
                used += 1
            parts.append(part_path)
            start += max(used, 1) + 1

    if not parts:
        return []

    produced = []
    if len(parts) == 1:
        dst = out_base + '.mp4'
        os.replace(parts[0], dst)
        produced.append(dst)
    else:
        for n, part in enumerate(parts, 1):
            dst = f'{out_base}-parte{n:02d}.mp4'
            os.replace(part, dst)
            produced.append(dst)
    return produced
