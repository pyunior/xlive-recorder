import os
import sys
import streamonitor.config as config
from streamonitor.managers.bulk_status_manager import BulkStatusManager
from streamonitor.managers.httpmanager import HTTPManager
from streamonitor.managers.climanager import CLIManager
from streamonitor.managers.zmqmanager import ZMQManager
from streamonitor.managers.outofspace_detector import OOSDetector
from streamonitor.managers.watchdog import WatchdogManager
from streamonitor.video import JobQueue
from streamonitor.clean_exit import CleanExit
import streamonitor.sites  # must have
import parameters
import signal
import threading

        
def is_docker():
    path = '/proc/self/cgroup'
    return (
        os.path.exists('/.dockerenv') or
        os.path.isfile(path) and any('docker' in line for line in open(path))
    )


def main():
    if not OOSDetector.disk_space_good():
        print(OOSDetector.under_threshold_message)
        sys.exit(1)

    streamers = config.loadStreamers()

    clean_exit = CleanExit(streamers)

    oos_detector = OOSDetector(streamers)
    oos_detector.start()

    bulk_status_manager = BulkStatusManager(streamers)
    bulk_status_manager.start()

    if not is_docker() and sys.stdin.isatty():
        console_manager = CLIManager(streamers)
        console_manager.start()

    zmq_manager = ZMQManager(streamers)
    zmq_manager.start()

    # Cut/join jobs run one at a time on their own thread so ffmpeg never
    # competes with the live recordings for disk, and so a long job cannot
    # block an HTTP request.
    job_queue = JobQueue()
    job_queue.start()

    http_manager = HTTPManager(streamers)
    http_manager.start()


    # Auto-start all streamers when running without terminal (systemd)
    if not sys.stdin.isatty():
        for streamer in streamers:
            streamer.restart()

    # Started last, and it waits out a grace period before taking a baseline,
    # so the bots' first real status is in before it decides who is online.
    watchdog = None
    if parameters.WATCHDOG_ENABLED:
        watchdog = WatchdogManager(streamers)
        watchdog.start()

    # Wait for a shutdown signal, then hand the shutdown to CleanExit.
    #
    # The handler must not just release the main thread. Bot threads are not
    # daemons and loop until `quitting` is set, so returning from main() left
    # the interpreter waiting on 44 threads that never ended; systemd then
    # SIGKILLed us at TimeoutStopSec, cutting StripChat's .tmp.ts -> .mp4 remux
    # mid-write and orphaning the temp file. Registering these here also used
    # to overwrite the handlers CleanExit installs, so clean_exit() never ran.
    stop_event = threading.Event()

    def request_shutdown(signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    stop_event.wait()

    print('Shutting down: finalizing recordings...', flush=True)
    job_queue.stop()
    if watchdog:
        watchdog.stop()
    clean_exit()  # stops every bot and waits for each recording to be finalized
    print('Shutdown complete.', flush=True)

main()
