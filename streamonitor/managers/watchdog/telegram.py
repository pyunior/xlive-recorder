import queue
import threading
import time
from html import escape

import requests

import streamonitor.log as log

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3900  # Telegram caps at 4096; leave room for the continuation mark


def html_escape(text):
    return escape(str(text), quote=False)


def split_message(text, limit=MAX_LEN):
    """Split on line boundaries, falling back to hard cuts for long lines."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


class TelegramNotifier:
    """Background sender for Telegram messages.

    Sending happens on its own thread so a slow or unreachable API never
    stalls the watchdog poll loop, and so a burst of transitions (every model
    coming online at once in the morning) is paced out instead of tripping
    Telegram's per-chat flood limit.
    """

    def __init__(self, token, chat_id, min_interval=3.5, max_retries=4, enabled=True):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.enabled = enabled
        self.logger = log.Logger("watchdog_telegram")
        self.queue = queue.Queue()
        self.session = requests.Session()
        self._last_sent = 0.0
        self._warned = False
        self._stop = threading.Event()
        self._thread = None

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="watchdog-telegram", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def send(self, text):
        """Queue a message. Never blocks and never raises."""
        if not self.enabled:
            return False
        if not self.configured:
            if not self._warned:
                self.logger.warning(
                    "Telegram not configured: set STRMNTR_TG_TOKEN and STRMNTR_TG_CHAT_ID in .env")
                self._warned = True
            return False
        for chunk in split_message(text):
            self.queue.put(chunk)
        return True

    def send_now(self, text):
        """Send synchronously. Returns (ok, detail); used by the test command."""
        if not self.configured:
            return False, "Telegram not configured (STRMNTR_TG_TOKEN / STRMNTR_TG_CHAT_ID)"
        ok = True
        detail = "sent"
        for chunk in split_message(text):
            ok, detail = self._deliver(chunk)
            if not ok:
                break
        return ok, detail

    def _run(self):
        while not self._stop.is_set():
            try:
                chunk = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            wait = self.min_interval - (time.time() - self._last_sent)
            if wait > 0:
                self._stop.wait(wait)
            ok, detail = self._deliver(chunk)
            if not ok:
                self.logger.error(f"Dropping Telegram message after retries: {detail}")
            self.queue.task_done()

    def _deliver(self, text):
        url = API.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        delay = 2
        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(url, data=payload, timeout=20)
                self._last_sent = time.time()
                if resp.status_code == 200:
                    return True, "sent"
                if resp.status_code == 429:
                    retry_after = 5
                    try:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                    except Exception:
                        pass
                    self.logger.warning(f"Telegram rate limit, waiting {retry_after}s")
                    self._stop.wait(retry_after + 1)
                    continue
                if 400 <= resp.status_code < 500:
                    # Bad token, wrong chat id, malformed HTML: retrying will not help.
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last = str(e)
            if self._stop.wait(delay):
                return False, "shutting down"
            delay = min(delay * 2, 60)
        return False, last
