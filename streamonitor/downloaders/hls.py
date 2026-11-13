import m3u8
import os
import shutil
import subprocess
from threading import Thread
from ffmpy import FFmpeg, FFRuntimeError
from time import sleep
from parameters import DEBUG, CONTAINER, SEGMENT_TIME, FFMPEG_PATH

_http_lib = None
if not _http_lib:
    try:
        import pycurl_requests as requests
        _http_lib = 'pycurl'
    except ImportError:
        pass
if not _http_lib:
    try:
        import requests
        _http_lib = 'requests'
    except ImportError:
        pass
if not _http_lib:
    raise ImportError("Please install requests or pycurl package to proceed")


def getVideoNativeHLS(self, url, filename, m3u_processor=None):
    self.stopDownloadFlag = False
    error = False
    tmpfilename = filename[:-len('.' + CONTAINER)] + '.tmp.ts'
    session = requests.Session()

    def execute():
        nonlocal error
        downloaded_list = []
        with open(tmpfilename, 'wb') as outfile:
            did_download = False
            while not self.stopDownloadFlag:
                r = session.get(url, headers=self.headers, cookies=self.cookies)
                content = r.content.decode("utf-8")
                if m3u_processor:
                    content = m3u_processor(content)
                chunklist = m3u8.loads(content)
                if len(chunklist.segments) == 0:
                    return
                for chunk in chunklist.segment_map + chunklist.segments:
                    if chunk.uri in downloaded_list:
                        continue
                    did_download = True
                    downloaded_list.append(chunk.uri)
                    chunk_uri = chunk.uri
                    self.debug('Downloading ' + chunk_uri)
                    if not chunk_uri.startswith("https://"):
                        chunk_uri = '/'.join(url.split('.m3u8')[0].split('/')[:-1]) + '/' + chunk_uri
                    m = session.get(chunk_uri, headers=self.headers, cookies=self.cookies)
                    if m.status_code != 200:
                        return
                    outfile.write(m.content)
                    if self.stopDownloadFlag:
                        return
                if not did_download:
                    sleep(10)

    def terminate():
        self.stopDownloadFlag = True

    process = Thread(target=execute)
    process.start()
    self.stopDownload = terminate
    process.join()
    self.stopDownload = None

    if error:
        return False

    if not os.path.exists(tmpfilename):
        return False

    if os.path.getsize(tmpfilename) == 0:
        os.remove(tmpfilename)
        return False

    # Post-processing
    #
    # The temp file is fragmented MP4 (the site serves fMP4/CMAF segments and
    # the loop above just concatenates them), so the input needs
    # `-fflags +ignidx`: without it the mov demuxer tries to build a sample
    # index out of the concatenated per-segment indexes, gives up with
    # "error reading header", and ffmpy raises. That used to return False and
    # leave the .tmp.ts behind -- the whole recording stranded in a file
    # nothing could open. See fmp4_repair for the full story.
    tmpsize = os.path.getsize(tmpfilename)
    try:
        stdout = open(filename + '.postprocess_stdout.log', 'w+') if DEBUG else subprocess.DEVNULL
        stderr = open(filename + '.postprocess_stderr.log', 'w+') if DEBUG else subprocess.DEVNULL
        output_str = '-c:a copy -c:v copy'
        suffix = ''
        if SEGMENT_TIME is not None:
            output_str += f' -f segment -reset_timestamps 1 -segment_time {str(SEGMENT_TIME)}'
            if hasattr(self, 'filename_extra_suffix'):
                suffix = self.filename_extra_suffix
            filename = filename[:-len('.' + CONTAINER)] + '_%03d' + suffix + '.' + CONTAINER
        ff = FFmpeg(executable=FFMPEG_PATH,
                    inputs={tmpfilename: '-fflags +ignidx'},
                    outputs={filename: output_str})
        ff.run(stdout=stdout, stderr=stderr)
    except FFRuntimeError as e:
        if e.exit_code and e.exit_code != 255:
            self.logger.warning(
                f'El remux falló (código {e.exit_code}); intentando reparar')

    # ffmpeg exits non-zero when the tail is damaged even though it wrote
    # everything up to that point, so judge the result, not the exit code.
    recovered = _recovered_bytes(filename)
    if recovered >= tmpsize * 0.9:
        os.remove(tmpfilename)
        return True

    # Either nothing came out or a damaged fragment cut it short. Resume past
    # the damage instead of abandoning the rest of the show.
    self.logger.warning(
        f'Remux incompleto ({_human(recovered)} de {_human(tmpsize)}); reparando')
    if _repair_tmpfile(self, tmpfilename, filename, recovered):
        return True

    self.logger.error(
        f'No se pudo convertir la grabación; se conserva {os.path.basename(tmpfilename)} '
        f'({_human(tmpsize)}) para recuperarla a mano')
    return False


def _human(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024


def _recovered_bytes(filename):
    """Bytes written by the remux, counting segment outputs too."""
    if '%03d' in filename:
        import glob
        pattern = filename.replace('%03d', '*')
        return sum(os.path.getsize(f) for f in glob.glob(pattern) if os.path.isfile(f))
    return os.path.getsize(filename) if os.path.exists(filename) else 0


def _repair_tmpfile(self, tmpfilename, filename, already):
    """Deep recovery for a temp file the normal remux could not finish.

    Keeps whichever result holds more media and removes the temp file only
    when the recording has actually been rescued.
    """
    from streamonitor.downloaders.fmp4_repair import repair

    base = tmpfilename[:-len('.tmp.ts')] if tmpfilename.endswith('.tmp.ts') \
        else os.path.splitext(tmpfilename)[0]
    workdir = base + '.repair'
    try:
        produced = repair(tmpfilename, base + '-rec', workdir)
    except Exception as e:
        self.logger.error(f'La reparación falló: {e}')
        produced = []
    finally:
        if os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)

    if not produced:
        return False

    gained = sum(os.path.getsize(f) for f in produced)
    if gained <= already:
        # The plain remux already had more; drop the repair's output.
        for f in produced:
            os.remove(f)
        if already > 0:
            os.remove(tmpfilename)
            return True
        return False

    # The repair won: discard the short remux output and keep these.
    if '%03d' in filename:
        import glob
        for f in glob.glob(filename.replace('%03d', '*')):
            os.remove(f)
    elif os.path.exists(filename):
        os.remove(filename)

    self.logger.info(
        f'Reparada: {len(produced)} fichero(s), {_human(gained)} recuperados')
    os.remove(tmpfilename)
    return True
