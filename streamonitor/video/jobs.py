"""Serialized queue for the cut/join jobs.

Why a queue instead of doing the work inside the HTTP request:

  * a 10 GB cut takes long enough for a browser to give up waiting;
  * several editors clicking at once would have ffmpeg processes competing for
    the same disk the live recordings are being written to;
  * most importantly, OOSDetector kills the whole process when free space drops
    below MIN_FREE_DISK_PERCENT. A cut briefly doubles a recording's footprint,
    so an unchecked job can take the recorder down. Every job is therefore
    space-checked before it starts, and refused with an explanation rather than
    deleting anything to make room.
"""
import itertools
import os
import threading
import time
from collections import deque

import streamonitor.log as log
from parameters import MIN_FREE_DISK_PERCENT
from streamonitor.video.ffmpeg_ops import FFmpegError, cut, join, probe

# Room to leave on top of the recorder's own hard floor, in percentage points.
SPACE_HEADROOM_PERCENT = 3.0
MAX_HISTORY = 200

_queue = None


def get_queue():
    return _queue


class Job:
    _ids = itertools.count(1)

    def __init__(self, kind, title, payload):
        self.id = next(Job._ids)
        self.kind = kind                 # 'cut' | 'join'
        self.title = title
        self.payload = payload
        self.state = 'en cola'           # en cola | procesando | listo | error
        self.message = ''
        self.results = []
        self.created = time.time()
        self.started = None
        self.finished = None

    def as_dict(self):
        return {
            'id': self.id, 'kind': self.kind, 'title': self.title,
            'state': self.state, 'message': self.message,
            'results': [os.path.basename(p) for p in self.results],
            'created': self.created, 'started': self.started,
            'finished': self.finished,
            'elapsed': round((self.finished or time.time()) - (self.started or self.created), 1),
        }


class JobQueue(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.name = 'video-jobs'
        self.logger = log.Logger('video_jobs')
        self._pending = deque()
        self._history = deque(maxlen=MAX_HISTORY)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self.current = None
        global _queue
        _queue = self

    # -- space guard ----------------------------------------------------

    @staticmethod
    def space_check(target_dir, needed_bytes):
        """-> (ok, message). Never deletes anything; the user decides."""
        import shutil
        usage = shutil.disk_usage(target_dir)
        floor = (MIN_FREE_DISK_PERCENT + SPACE_HEADROOM_PERCENT) / 100.0 * usage.total
        if usage.free - needed_bytes < floor:
            gb = lambda b: b / (1024 ** 3)
            return False, (
                f'Espacio insuficiente: el trabajo necesita {gb(needed_bytes):.1f} GB y '
                f'solo hay {gb(usage.free):.1f} GB libres, reservando '
                f'{gb(floor):.1f} GB para que el grabador no se detenga. '
                'Libera espacio moviendo o borrando material ya editado.')
        return True, ''

    # -- submission -----------------------------------------------------

    def submit_cut(self, path, minutes):
        info = probe(path)
        if info is None:
            raise FFmpegError('No se puede leer el vídeo de origen')
        outdir = os.path.dirname(os.path.abspath(path))
        ok, msg = self.space_check(outdir, info['size'])
        if not ok:
            raise FFmpegError(msg)
        job = Job('cut', f'Cortar {info["name"]} cada {minutes} min',
                  {'path': path, 'minutes': minutes})
        return self._enqueue(job)

    def submit_join(self, paths, output_name=None):
        total = 0
        for p in paths:
            if not os.path.isfile(p):
                raise FFmpegError(f'No existe: {os.path.basename(p)}')
            total += os.path.getsize(p)
        outdir = os.path.dirname(os.path.abspath(paths[0]))
        ok, msg = self.space_check(outdir, total)
        if not ok:
            raise FFmpegError(msg)
        name = output_name or (
            os.path.splitext(os.path.basename(paths[0]))[0] + '-unido.mp4')
        if not os.path.splitext(name)[1]:
            name += '.mp4'
        job = Job('join', f'Unir {len(paths)} vídeos en {name}',
                  {'paths': list(paths), 'output': os.path.join(outdir, name)})
        return self._enqueue(job)

    def _enqueue(self, job):
        with self._lock:
            self._pending.append(job)
            self._history.append(job)
        self._wake.set()
        self.logger.info(f'[{job.id}] en cola: {job.title}')
        return job

    # -- inspection -----------------------------------------------------

    def snapshot(self):
        with self._lock:
            jobs = [j.as_dict() for j in reversed(self._history)]
            pending = len(self._pending)
        return {'jobs': jobs, 'pending': pending,
                'current': self.current.id if self.current else None}

    def get(self, job_id):
        with self._lock:
            for j in self._history:
                if j.id == job_id:
                    return j
        return None

    # -- worker ---------------------------------------------------------

    def stop(self):
        self._stopping.set()
        self._wake.set()

    def run(self):
        self.logger.info('Cola de trabajos de vídeo lista')
        while not self._stopping.is_set():
            with self._lock:
                job = self._pending.popleft() if self._pending else None
            if job is None:
                self._wake.wait(1)
                self._wake.clear()
                continue
            self._execute(job)
        self.logger.info('Cola de trabajos detenida')

    def _execute(self, job):
        self.current = job
        job.state = 'procesando'
        job.started = time.time()
        self.logger.info(f'[{job.id}] {job.title}')
        try:
            if job.kind == 'cut':
                job.results = cut(job.payload['path'], job.payload['minutes'],
                                  progress=lambda m: self.logger.info(f'[{job.id}] {m}'))
                job.message = f'{len(job.results)} trozos generados'
            else:
                out, info = join(job.payload['paths'], job.payload['output'],
                                 progress=lambda m: self.logger.info(f'[{job.id}] {m}'))
                job.results = [out]
                mins = int(info['duration'] // 60)
                job.message = f'{mins} min en un fichero'
            job.state = 'listo'
            self.logger.info(f'[{job.id}] listo: {job.message}')
        except FFmpegError as e:
            job.state, job.message = 'error', str(e)
            self.logger.error(f'[{job.id}] {e}')
        except Exception as e:
            job.state, job.message = 'error', f'Error inesperado: {e}'
            self.logger.exception(e)
        finally:
            job.finished = time.time()
            self.current = None
