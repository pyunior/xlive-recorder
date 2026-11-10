import math
from logging import INFO, WARN, ERROR
from threading import Thread
from termcolor import colored
import terminaltables.terminal_io
from terminaltables import AsciiTable
import streamonitor.config as config
import streamonitor.log as log
from streamonitor.bot import Bot, LOADED_SITES
from streamonitor.managers.outofspace_detector import OOSDetector
from streamonitor.managers.watchdog import get_watchdog

from streamonitor.enums import Status


class Manager(Thread):
    def __init__(self, streamers):
        super().__init__()
        self.daemon = True
        self.streamers = streamers
        self.logger = log.Logger("manager")

    def execCmd(self, line):
        parts = str(line).split(' ')
        if 'do_' + parts[0] not in dir(self):
            return 'Unknown command'

        command = getattr(self, 'do_' + parts[0])
        if command:
            username = parts[1] if len(parts) > 1 else ""
            site = parts[2] if len(parts) > 2 else ""
            streamer = self.getStreamer(username, site)
            return command(streamer, username, site)

    def getStreamer(self, username, site):
        found = None
        site = Bot.str2site(site)
        if site:
            site = site.site
        for streamer in self.streamers:
            if streamer.username == username:
                if site and site != "":
                    if streamer.site == site:
                        return streamer
                else:
                    if not found:
                        found = streamer
                    else:
                        self.logger.error('Multiple users exist with this username, specify site too')
                        return None
        return found

    def saveConfig(self):
        config.save_config([s.export() for s in self.streamers])

    def do_add(self, streamer, username, site):
        if streamer:
            return 'Streamer already exists'
        elif username and site:
            try:
                streamer = Bot.createInstance(username, site)
                self.streamers.append(streamer)
                streamer.start()
                streamer.restart()
                self.saveConfig()
                return "Added [" + streamer.siteslug + "] " + streamer.username
            except Exception as e:
                return f"Failed to add: {e}"
        else:
            return "Missing value(s)"

    def do_remove(self, streamer, username, site):
        if not streamer:
            return "Streamer not found"
        try:
            streamer.stop(None, None)
            streamer.logger.handlers = []
            self.streamers.remove(streamer)
            self.saveConfig()
            return "OK"
        except Exception as e:
            self.logger.error(e)
            return "Failed to remove streamer"

    def do_start(self, streamer, username, site):
        if not streamer:
            if username == '*':
                for streamer in self.streamers:
                    if not streamer.is_alive():
                        streamer.start()
                    streamer.restart()
                self.saveConfig()
                return "Started all"
            else:
                return "Streamer not found"
        else:
            try:
                if not streamer.is_alive():
                    streamer.start()
                streamer.restart()
                self.saveConfig()
                return "OK"
            except Exception as e:
                self.logger.error(e)
                return "Failed to start"

    def do_stop(self, streamer, username, site):
        if not streamer:
            if username == '*':
                for streamer in self.streamers:
                    streamer.stop(None, None)
                self.saveConfig()
                return "Stopped all"
            else:
                return "Streamer not found"
        else:
            try:
                streamer.stop(None, None)
                self.saveConfig()
                return "OK"
            except Exception as e:
                self.logger.error(e)
                return "Failed to stop"

    def do_restart(self, streamer, username, site):
        if not streamer:
            return "Streamer not found"
        self.do_stop(streamer, username, site)
        return self.do_start(streamer, username, site)
        
    def do_status(self, streamer, username, site):
        output = [["Username", "Site", "Started", "Status"]]

        def line():
            output.append([streamer.username,
                           streamer.site,
                           streamer.running,
                           streamer.status()])

        if streamer:
            line()
        else:
            for streamer in self.streamers:
                line()
        return "Status:\n" + f'Free space: {str(round(OOSDetector.free_space(), 3))}%\n\n' + AsciiTable(output).table

    def do_status2(self, streamer, username, site):
        maxlen = max([len(s.username) for s in self.streamers] or [0])
        termwidth = terminaltables.terminal_io.terminal_size()[0]
        table_nx = math.floor(termwidth/(maxlen+3))
        output = ''
        output += 'Status:\n'

        for site in LOADED_SITES:
            output += site.site + '\n'
            output += ('+' + '-'*(maxlen+2))*table_nx + '+\n'
            site_name = site.site
            i = 0
            for streamer in self.streamers:
                if streamer.site == site_name:
                    output += '!'
                    status_color = None
                    status = streamer.sc
                    if status == Status.PUBLIC: status_color = 'green'
                    if status == Status.PRIVATE: status_color = 'magenta'
                    if status == Status.ERROR: status_color = 'red'
                    if not streamer.running: status_color = 'grey'
                    output += colored(' ' + streamer.username + ' '*(maxlen-len(streamer.username)) + ' ', status_color)
                    i += 1
                    if i == table_nx:
                        output += '!\n'
                        i = 0
            for r in range(i, table_nx):
                output += '! ' + ' ' * maxlen + ' '
            output += '!\n'
            output += ('+' + '-'*(maxlen+2))*table_nx + '+\n'
            output += '\n'
        return output

    # -- watchdog ---------------------------------------------------------
    # execCmd passes the 2nd word as `username`, so it doubles as the argument
    # for these (e.g. "wdreport 2026-08-18").

    def do_wdstatus(self, streamer, username, site):
        watchdog = get_watchdog()
        if not watchdog:
            return "Watchdog is not running (STRMNTR_WATCHDOG=false?)"
        return watchdog.status_table()

    def do_wdreport(self, streamer, username, site):
        """Build and send the daily summary now. Optional arg: report day
        (YYYY-MM-DD); the window is the previous day's start hour to that
        day's start hour. Sending this by hand does not consume the
        scheduled one."""
        watchdog = get_watchdog()
        if not watchdog:
            return "Watchdog is not running (STRMNTR_WATCHDOG=false?)"
        try:
            return watchdog.send_report(username or None, mark=False)
        except ValueError:
            return "Invalid date, expected YYYY-MM-DD"

    def do_wdtest(self, streamer, username, site):
        watchdog = get_watchdog()
        if not watchdog:
            return "Watchdog is not running (STRMNTR_WATCHDOG=false?)"
        ok, detail = watchdog.telegram.send_now(
            "\u2705 <b>Streamonitor watchdog</b>\nPrueba de conexion con Telegram: OK")
        return "Telegram OK" if ok else f"Telegram FAILED - {detail}"
