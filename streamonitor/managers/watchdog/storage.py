import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    site    TEXT    NOT NULL,
    room    TEXT    NOT NULL,
    online  INTEGER NOT NULL,
    status  TEXT    NOT NULL,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_room_ts ON events (site, room, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS heartbeats (
    ts REAL PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class WatchdogStore:
    """Event log for room connect/disconnect transitions.

    Only transitions are stored, so a room that stays online for eight hours
    costs two rows. Heartbeats are stored separately and are what let the
    report tell "the room was offline" apart from "we were not watching":
    without them a crash would be billed to the models as airtime.
    """

    def __init__(self, path):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    # -- events ---------------------------------------------------------

    def add_event(self, site, room, online, status, ts=None):
        ts = time.time() if ts is None else ts
        with self._lock:
            self._db.execute(
                "INSERT INTO events (site, room, online, status, ts) VALUES (?, ?, ?, ?, ?)",
                (site, room, 1 if online else 0, status, ts))
            self._db.commit()
        return ts

    def last_event(self, site, room, before=None):
        sql = "SELECT online, status, ts FROM events WHERE site = ? AND room = ?"
        args = [site, room]
        if before is not None:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def events_between(self, site, room, start, end):
        with self._lock:
            rows = self._db.execute(
                "SELECT online, status, ts FROM events "
                "WHERE site = ? AND room = ? AND ts >= ? AND ts < ? "
                "ORDER BY ts ASC, id ASC",
                (site, room, start, end)).fetchall()
        return [dict(r) for r in rows]

    def known_rooms(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT site, room FROM events").fetchall()
        return [(r['site'], r['room']) for r in rows]

    # -- heartbeats -----------------------------------------------------

    def heartbeat(self, ts=None):
        ts = time.time() if ts is None else ts
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO heartbeats (ts) VALUES (?)", (ts,))
            self._db.commit()
        return ts

    def downtime_gaps(self, start, end, threshold):
        """Windows inside [start, end) where the watchdog was not running.

        A missing heartbeat run longer than `threshold` counts as downtime,
        as does a missing head or tail (started late / stopped early).
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT ts FROM heartbeats WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                (start - threshold, end)).fetchall()
        beats = [r['ts'] for r in rows]
        beats = [b for b in beats if b <= end]

        gaps = []
        if not beats:
            return [(start, end)] if end > start else []

        first = max(beats[0], start)
        if first > start:
            gaps.append((start, first))
        for a, b in zip(beats, beats[1:]):
            if b - a > threshold:
                gap_start, gap_end = max(a, start), min(b, end)
                if gap_end > gap_start:
                    gaps.append((gap_start, gap_end))
        last = beats[-1]
        if last < end:
            gaps.append((max(last, start), end))
        return [(a, b) for a, b in gaps if b > a]

    # -- meta -----------------------------------------------------------

    def get_meta(self, key, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default

    def set_meta(self, key, value):
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)))
            self._db.commit()

    # -- housekeeping ---------------------------------------------------

    def prune(self, before_ts):
        """Drop old heartbeats, and old events that are no longer the state
        of record for their room (the newest event per room always stays,
        otherwise a long-idle room would lose its baseline)."""
        with self._lock:
            self._db.execute("DELETE FROM heartbeats WHERE ts < ?", (before_ts,))
            self._db.execute(
                "DELETE FROM events WHERE ts < ? AND id NOT IN "
                "(SELECT MAX(id) FROM events GROUP BY site, room)",
                (before_ts,))
            self._db.commit()
