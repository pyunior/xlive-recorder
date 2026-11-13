import time
from datetime import datetime, timedelta, timezone
from threading import Thread, Event

from terminaltables import AsciiTable

import parameters
import streamonitor.log as log
from streamonitor.enums import Status
from streamonitor.managers.watchdog.report import (
    build_report, fmt_datetime, fmt_duration, window_for)
from streamonitor.managers.watchdog.storage import WatchdogStore
from streamonitor.managers.watchdog.telegram import TelegramNotifier, html_escape

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

# Statuses that mean "we could not tell". They must not be logged as a
# disconnect: a rate limit or a transient error is not the model going away.
INDETERMINATE = {Status.UNKNOWN, Status.ERROR, Status.RATELIMIT, Status.BLOCKED}

HEARTBEAT_INTERVAL = 60
HEARTBEAT_GAP_THRESHOLD = 240

_instance = None


def get_watchdog():
    return _instance


def _timezone(name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    log.Logger("watchdog").warning(
        f"Timezone '{name}' unavailable, falling back to fixed UTC-5 (Colombia)")
    return timezone(timedelta(hours=-5))


class WatchdogManager(Thread):
    """Watches every room's status and reports connects/disconnects.

    It reads the status the site bots already maintain (`bot.sc`) rather than
    polling the sites again, so it adds no extra load and cannot disagree with
    what the recorder is doing.
    """

    def __init__(self, streamers):
        super().__init__()
        self.daemon = True
        self.name = "watchdog"
        self.streamers = streamers
        self.logger = log.Logger("watchdog")
        self._stopping = Event()

        self.tz = _timezone(parameters.WATCHDOG_TIMEZONE)
        self.poll_interval = max(1, parameters.WATCHDOG_POLL_INTERVAL)
        self.debounce_online = parameters.WATCHDOG_DEBOUNCE_ONLINE
        self.debounce_offline = parameters.WATCHDOG_DEBOUNCE_OFFLINE
        self.unknown_timeout = parameters.WATCHDOG_UNKNOWN_TIMEOUT
        self.report_hour = parameters.WATCHDOG_REPORT_HOUR
        self.report_minute = parameters.WATCHDOG_REPORT_MINUTE
        self.day_start_hour = parameters.WATCHDOG_DAY_START_HOUR
        self.alerts_enabled = parameters.WATCHDOG_ALERTS
        self.alert_on_start = parameters.WATCHDOG_ALERT_ON_START
        self.startup_grace = parameters.WATCHDOG_STARTUP_GRACE

        self.online_statuses = self._parse_statuses(parameters.WATCHDOG_ONLINE_STATUSES)

        self.store = WatchdogStore(parameters.WATCHDOG_DB)
        self.telegram = TelegramNotifier(
            parameters.TELEGRAM_BOT_TOKEN,
            parameters.TELEGRAM_CHAT_ID,
            min_interval=parameters.TELEGRAM_MIN_INTERVAL,
            enabled=self.alerts_enabled)

        # key -> {'online': bool, 'since': ts, 'pending': bool|None,
        #         'pending_since': ts, 'unknown_since': ts|None}
        self.state = {}
        self._last_heartbeat = 0.0
        self._last_prune = 0.0

        global _instance
        _instance = self

    def _parse_statuses(self, raw):
        wanted = set()
        for name in str(raw).split(','):
            name = name.strip().upper()
            if not name:
                continue
            status = getattr(Status, name, None)
            if status is None:
                self.logger.warning(f"Unknown status '{name}' in WATCHDOG_ONLINE_STATUSES, ignoring")
            else:
                wanted.add(status)
        return wanted or {Status.PUBLIC, Status.PRIVATE}

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _key(bot):
        return bot.site, bot.username

    def _observe(self, bot):
        """Map a bot's status to True (online), False (offline) or None (unknown)."""
        sc = bot.sc
        if sc in self.online_statuses:
            return True
        if sc in INDETERMINATE:
            return None
        return False

    def _rooms(self):
        return [(b.site, b.username, b.siteslug, getattr(b, 'url', None))
                for b in list(self.streamers)]

    # -- lifecycle ------------------------------------------------------

    def stop(self):
        self._stopping.set()
        self.telegram.stop()

    def run(self):
        self.telegram.start()
        self.logger.info(
            f"Watchdog activo · {len(self.streamers)} salas · zona {parameters.WATCHDOG_TIMEZONE} · "
            f"resumen diario {self.report_hour:02d}:{self.report_minute:02d} "
            f"(ventana {self.day_start_hour:02d}:00 → {self.day_start_hour:02d}:00)")
        if not self.telegram.configured:
            self.logger.warning(
                "Sin credenciales de Telegram: se registran los eventos pero no se envian avisos")

        if self.startup_grace > 0:
            self.logger.info(f"Esperando {self.startup_grace}s a que los bots reporten estado inicial")
            if self._stopping.wait(self.startup_grace):
                return
        self._seed_state()
        self._catch_up_report()

        while not self._stopping.is_set():
            try:
                self._tick()
            except Exception as e:
                self.logger.exception(e)
            self._stopping.wait(self.poll_interval)

        self.store.close()

    def _seed_state(self):
        """Adopt each room's current status without alerting.

        A restart must not fire 44 "connected" messages, and if a room's state
        changed while we were down we record the change silently so the hour
        accounting stays continuous.
        """
        now = time.time()
        for bot in list(self.streamers):
            key = self._key(bot)
            observed = self._observe(bot)
            last = self.store.last_event(bot.site, bot.username)
            if observed is None:
                # Unknown at boot: trust what the log says, or assume offline.
                current = bool(last['online']) if last else False
            else:
                current = observed
            if last is None or bool(last['online']) != current:
                self.store.add_event(bot.site, bot.username, current, bot.sc.name, now)
                since = now
            else:
                since = last['ts']
            self.state[key] = {
                'online': current, 'since': since,
                'pending': None, 'pending_since': None, 'unknown_since': None,
            }
        self.store.heartbeat(now)
        self._last_heartbeat = now
        online_now = sum(1 for s in self.state.values() if s['online'])
        self.logger.info(f"Estado inicial: {online_now} en linea de {len(self.state)} salas")
        if self.alert_on_start and online_now:
            names = ", ".join(f"<code>{html_escape(r)}</code>"
                              for (_, r), s in self.state.items() if s['online'])
            self.telegram.send(f"🔄 <b>Watchdog reiniciado</b>\n🟢 En linea ahora ({online_now}): {names}")

    # -- polling --------------------------------------------------------

    def _tick(self):
        now = time.time()
        bots = list(self.streamers)

        live = {self._key(b) for b in bots}
        for gone in set(self.state) - live:
            # Removed with `remove`; its history stays in the log for reports.
            del self.state[gone]

        for bot in bots:
            key = self._key(bot)
            if key not in self.state:
                # Room added at runtime: adopt it silently, like at boot.
                observed = self._observe(bot)
                current = bool(observed)
                self.store.add_event(bot.site, bot.username, current, bot.sc.name, now)
                self.state[key] = {'online': current, 'since': now, 'pending': None,
                                   'pending_since': None, 'unknown_since': None}
                continue
            self._evaluate(bot, self.state[key], now)

        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            self.store.heartbeat(now)
            self._last_heartbeat = now

        self._maybe_report(now)
        self._maybe_prune(now)

    def _evaluate(self, bot, st, now):
        observed = self._observe(bot)
        changed_at = now

        if observed is None:
            # Hold the last known state; only give up after a long silence.
            if st['unknown_since'] is None:
                st['unknown_since'] = now
            if now - st['unknown_since'] < self.unknown_timeout:
                return
            # Out of patience: call it offline, but date it to the last status
            # we could actually trust. Crediting a long error window as airtime
            # would inflate the room's hours.
            observed = False
            changed_at = st['unknown_since']
        else:
            st['unknown_since'] = None

        if observed == st['online']:
            st['pending'] = None
            st['pending_since'] = None
            return

        if st['pending'] != observed:
            # First sighting of the new state: remember when it actually began.
            st['pending'] = observed
            st['pending_since'] = changed_at
            return

        debounce = self.debounce_online if observed else self.debounce_offline
        if now - st['pending_since'] < debounce:
            return

        # Confirmed. Bill the change to when it started, not when we confirmed it.
        at = st['pending_since']
        previous_since = st['since']
        st['online'] = observed
        st['since'] = at
        st['pending'] = None
        st['pending_since'] = None

        self.store.add_event(bot.site, bot.username, observed, bot.sc.name, at)
        self._alert(bot, observed, at, at - previous_since)

    def _alert(self, bot, online, at, elapsed):
        name = html_escape(bot.username)
        url = getattr(bot, 'url', None)
        label = (f'<a href="{html_escape(url)}">{name}</a>'
                 if url and str(url).startswith('http') else f"<code>{name}</code>")
        clock = datetime.fromtimestamp(at, self.tz).strftime("%H:%M:%S")
        slug = html_escape(bot.siteslug)

        if online:
            msg = (f"🟢 <b>CONECTADA</b> · {label} [{slug}]\n"
                   f"🕒 {clock} · offline previo {fmt_duration(elapsed)}")
        else:
            msg = (f"🔴 <b>DESCONECTADA</b> · {label} [{slug}]\n"
                   f"🕒 {clock} · transmitio {fmt_duration(elapsed)}")
        self.logger.info(f"{'CONECTADA' if online else 'DESCONECTADA'} "
                         f"[{bot.siteslug}] {bot.username} ({fmt_duration(elapsed)})")
        self.telegram.send(msg)

    # -- daily report ---------------------------------------------------

    def _report_due_for(self, now):
        """The date whose report should already have gone out, or None."""
        local = datetime.fromtimestamp(now, self.tz)
        deadline = local.replace(hour=self.report_hour, minute=self.report_minute,
                                 second=0, microsecond=0)
        if local < deadline:
            return None
        day = local.date().isoformat()
        return None if self.store.get_meta('last_report_day') == day else day

    def _catch_up_report(self):
        """If we were down over the scheduled hour, send it as soon as we're up."""
        due = self._report_due_for(time.time())
        if due:
            self.logger.info(f"Resumen de {due} pendiente, enviandolo ahora")
            self.send_report(due, skip_if_blind=True)

    def _maybe_report(self, now):
        due = self._report_due_for(now)
        if due:
            self.send_report(due, skip_if_blind=True)

    def _maybe_prune(self, now):
        if now - self._last_prune < 6 * 3600:
            return
        self._last_prune = now
        try:
            self.store.prune(now - parameters.WATCHDOG_RETENTION_DAYS * 86400)
        except Exception as e:
            self.logger.warning(f"No se pudo limpiar la base del watchdog: {e}")

    def build_report_text(self, day=None):
        """day: 'YYYY-MM-DD', the day the report is published on.
        Returns (text, window_start, window_end, coverage) where coverage is
        the fraction of the window the watchdog was actually up for."""
        if day is None:
            report_day = datetime.now(self.tz).date()
        elif isinstance(day, str):
            report_day = datetime.strptime(day, "%Y-%m-%d").date()
        else:
            report_day = day
        t0, t1 = window_for(report_day, self.day_start_hour, self.tz)
        gaps = self.store.downtime_gaps(t0, t1, HEARTBEAT_GAP_THRESHOLD)
        downtime = sum(b - a for a, b in gaps)
        coverage = max(0.0, 1.0 - downtime / max(1.0, t1 - t0))
        text = build_report(self.store, self._rooms(), t0, t1, self.tz,
                            HEARTBEAT_GAP_THRESHOLD)
        return text, t0, t1, coverage

    def send_report(self, day=None, mark=True, skip_if_blind=False):
        try:
            text, t0, t1, coverage = self.build_report_text(day)
        except Exception as e:
            self.logger.exception(e)
            return f"Error generando el resumen: {e}"

        window = f"{fmt_datetime(t0, self.tz)} → {fmt_datetime(t1, self.tz)}"
        if skip_if_blind and coverage < 0.02:
            # First run, or the watchdog was down for essentially the whole
            # window. An all-zeroes report would read as "nobody worked".
            if mark and day:
                self.store.set_meta('last_report_day', day)
            self.logger.info(f"Resumen {window} omitido: sin datos en la ventana "
                             f"(cobertura {coverage * 100:.0f}%)")
            return "Sin datos en la ventana, resumen omitido"

        sent = self.telegram.send(text)
        if mark and day:
            self.store.set_meta('last_report_day', day)
        self.logger.info(
            f"Resumen {window} (cobertura {coverage * 100:.0f}%) "
            f"{'enviado a Telegram' if sent else 'generado (Telegram no configurado)'}")
        return text

    # -- introspection --------------------------------------------------

    def status_table(self):
        now = time.time()
        rows = [["Sala", "Sitio", "Estado", "Desde", "Tiempo", "Pendiente"]]
        entries = sorted(self.state.items(), key=lambda kv: (not kv[1]['online'], kv[0][1].lower()))
        for (site, room), st in entries:
            pending = ""
            if st['pending'] is not None:
                pending = f"{'ON' if st['pending'] else 'OFF'} en {fmt_duration(max(0, (self.debounce_online if st['pending'] else self.debounce_offline) - (now - st['pending_since'])))}"
            rows.append([
                room, site,
                "EN LINEA" if st['online'] else "offline",
                datetime.fromtimestamp(st['since'], self.tz).strftime("%d/%m %H:%M"),
                fmt_duration(now - st['since']),
                pending,
            ])
        online = sum(1 for s in self.state.values() if s['online'])
        header = (f"Watchdog: {online} en linea de {len(self.state)} · "
                  f"Telegram: {'ok' if self.telegram.configured else 'NO CONFIGURADO'} · "
                  f"cola {self.telegram.queue.qsize()}\n\n")
        return header + AsciiTable(rows).table
