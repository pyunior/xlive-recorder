import logging
import os
import os.path
import shutil
from time import sleep
import streamonitor.log as log
from threading import Thread
from parameters import DOWNLOADS_DIR, MIN_FREE_DISK_PERCENT, MIN_FREE_DISK_GB
from streamonitor.clean_exit import CleanExit


class OOSDetector(Thread):
    # Kept for callers that still reference it; prefer threshold_message(),
    # which says which filesystem was measured and with what numbers.
    under_threshold_message = 'Free space is under threshold. Exiting.'

    def __init__(self, streamers):
        super().__init__()
        self.streamers = streamers
        self.daemon = True
        self.logger = log.Logger("out_of_space_detector")

    @staticmethod
    def free_space():
        usage = OOSDetector.space_usage()
        free_percent = usage.free / usage.total * 100
        return free_percent

    @staticmethod
    def measured_path():
        """The path actually measured.

        When DOWNLOADS_DIR does not exist this silently fell back to the
        install directory, so the check would report on the system disk while
        the operator stared at an empty 4 TB drive. The fallback stays (it
        keeps the check working before the folder is created) but now it is
        visible in the message.
        """
        if os.path.exists(DOWNLOADS_DIR):
            return DOWNLOADS_DIR, False
        return '.', True

    @staticmethod
    def space_usage():
        path, _ = OOSDetector.measured_path()
        return shutil.disk_usage(path)

    @staticmethod
    def disk_space_good():
        usage = OOSDetector.space_usage()
        # The percentage floor is useless on a big disk: 5% of 4 TB reserves
        # 200 GB, so the recorder refuses to start with 150 GB free -- enough
        # for ~55 hours. MIN_FREE_DISK_GB lets an absolute floor satisfy the
        # check instead. Off by default, so nothing changes unless it is set.
        if MIN_FREE_DISK_GB > 0 and usage.free >= MIN_FREE_DISK_GB * (1024 ** 3):
            return True
        return usage.free / usage.total * 100 > MIN_FREE_DISK_PERCENT

    # Filesystems that live in RAM. Recording into one of these eats memory
    # until the machine falls over, and everything is lost on reboot.
    VOLATILE_FSTYPES = frozenset({'tmpfs', 'ramfs', 'devtmpfs'})

    @staticmethod
    def filesystem_type(path):
        """Filesystem type backing `path`, via the longest matching mount."""
        try:
            target = os.path.abspath(path)
            best, best_type = '', None
            with open('/proc/mounts') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mount, fstype = parts[1].replace('\\040', ' '), parts[2]
                    if (target == mount or target.startswith(mount.rstrip('/') + '/')) \
                            and len(mount) > len(best):
                        best, best_type = mount, fstype
            return best_type
        except Exception:
            return None

    @staticmethod
    def storage_sanity():
        """-> (ok, message). Refuses to record into RAM.

        This is not hypothetical: on a new install the recordings path sat
        under /run because the folder had been created before the external
        drive was mounted, so udisks mounted the drive one name over
        ("Nuevo vol1") and the recorder quietly filled 1.5 GB of RAM while
        3.5 TB sat untouched.
        """
        path, fell_back = OOSDetector.measured_path()
        fstype = OOSDetector.filesystem_type(path)
        if fstype in OOSDetector.VOLATILE_FSTYPES:
            usage = shutil.disk_usage(path)
            return False, (
                f'The recordings folder is on a RAM filesystem and cannot be used: '
                f'{os.path.abspath(path)} is {fstype} '
                f'({usage.total / 1024 ** 3:.1f} GB total). Recording there would '
                f'fill memory and be lost on reboot. '
                f'STRMNTR_DOWNLOAD_DIR is "{DOWNLOADS_DIR}"'
                + ('; that path does not exist, so this is the install disk'
                   if fell_back else
                   '. A path under /run/media only exists while a user session '
                   'has the drive mounted, and the mount name shifts (e.g. '
                   '"Nuevo vol" -> "Nuevo vol1") when the folder already exists. '
                   'Mount the drive somewhere stable via /etc/fstab and point '
                   'STRMNTR_DOWNLOAD_DIR there'))
        return True, ''

    @staticmethod
    def threshold_message():
        path, fell_back = OOSDetector.measured_path()
        usage = shutil.disk_usage(path)
        gb = 1024 ** 3
        msg = (f'Free space is under threshold. Exiting. '
               f'Measured {os.path.abspath(path)}: '
               f'{usage.free / gb:.1f} GB free of {usage.total / gb:.1f} GB '
               f'({usage.free / usage.total * 100:.2f}%), '
               f'need more than {MIN_FREE_DISK_PERCENT}%')
        if MIN_FREE_DISK_GB > 0:
            msg += f' or at least {MIN_FREE_DISK_GB} GB'
        if fell_back:
            msg += (f'. NOTE: STRMNTR_DOWNLOAD_DIR="{DOWNLOADS_DIR}" does not exist, '
                    'so this is the install disk and not where you meant to record')
        return msg

    def run(self):
        while True:
            if not self.disk_space_good():
                self.logger.warning(OOSDetector.threshold_message())
                CleanExit(self.streamers)()
                # Salir de verdad. Esto corre dentro de un hilo, así que
                # sys.exit() solo terminaría ESTE hilo y el proceso quedaría
                # vivo sin grabar nada (systemd lo vería "active (running)"
                # indefinidamente y Restart=always nunca actuaría).
                # Pasó el 2026-07-15: 5 días sin grabar.
                logging.shutdown()
                os._exit(1)
            sleep(5)
