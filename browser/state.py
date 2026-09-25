"""browserd's own records, in one SQLite file, .run/state.db: the profiles, the sessions and their tabs.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import sqlite3
import threading
from typing import NamedTuple

from .profiles import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    folder TEXT NOT NULL UNIQUE COLLATE NOCASE,
    port INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL COLLATE NOCASE,
    label TEXT NOT NULL,
    started REAL NOT NULL,
    last_call REAL NOT NULL,
    closed REAL
);
CREATE TABLE IF NOT EXISTS tabs (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL COLLATE NOCASE,
    target TEXT NOT NULL,
    session TEXT,
    made REAL NOT NULL,
    closed REAL
);
CREATE INDEX IF NOT EXISTS tabs_by_target ON tabs (profile, target);
CREATE INDEX IF NOT EXISTS tabs_by_session ON tabs (session);
"""


class Session(NamedTuple):
    id: str
    profile: str  # the profile's name
    label: str
    started: float
    last_call: float  # when a call under it last started or ended
    closed: float | None


class Tab(NamedTuple):
    id: str
    profile: str
    target: str  # the DevTools target id in the profile's Chrome
    session: str | None  # None for a tab no session owns: opened by hand, or by such a tab's page
    made: float
    closed: float | None


class State:
    """state.db's one connection; its lock runs one statement at a time."""

    def __init__(self, path):
        """
        Args:
            path (str): the SQLite file, made with its tables when missing.
        """
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _all(self, sql, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _run(self, sql, *args):
        with self._lock:
            self._db.execute(sql, args)

    def profiles(self):
        """Every profile, by name."""
        return [Profile(*row) for row in self._all("SELECT name, folder, port FROM profiles ORDER BY name")]

    def profile(self, name):
        """The profile of that name, whatever its case, or None."""
        rows = self._all("SELECT name, folder, port FROM profiles WHERE name = ?", name)
        return Profile(*rows[0]) if rows else None

    def add_profile(self, profile):
        """Add a profile, or raise sqlite3.IntegrityError when its name, folder or port is taken."""
        self._run("INSERT INTO profiles (name, folder, port) VALUES (?, ?, ?)", *profile)

    def add_session(self, session):
        """Add a session, or raise sqlite3.IntegrityError when its id is taken."""
        self._run("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)", *session)

    def session(self, session_id):
        rows = self._all("SELECT * FROM sessions WHERE id = ?", session_id)
        return Session(*rows[0]) if rows else None

    def open_sessions(self):
        """Every session not closed, oldest first."""
        return [Session(*row) for row in self._all("SELECT * FROM sessions WHERE closed IS NULL ORDER BY started")]

    def touch(self, session_id, when):
        self._run("UPDATE sessions SET last_call = ? WHERE id = ?", when, session_id)

    def add_tab(self, tab):
        """Add a tab, or raise sqlite3.IntegrityError when its id was ever used."""
        self._run("INSERT INTO tabs VALUES (?, ?, ?, ?, ?, ?)", *tab)

    def tab(self, tab_id):
        rows = self._all("SELECT * FROM tabs WHERE id = ?", tab_id)
        return Tab(*rows[0]) if rows else None

    def tab_for_target(self, profile, target):
        """The newest tab, open or closed, a profile's Chrome target was given, or None."""
        rows = self._all("SELECT * FROM tabs WHERE profile = ? AND target = ? ORDER BY made DESC LIMIT 1", profile, target)
        return Tab(*rows[0]) if rows else None

    def open_tabs(self, profile):
        """Every tab of a profile not closed."""
        return [Tab(*row) for row in self._all("SELECT * FROM tabs WHERE profile = ? AND closed IS NULL", profile)]

    def session_tabs(self, session_id):
        """Every tab of a session not closed, oldest first."""
        return [Tab(*row) for row in self._all("SELECT * FROM tabs WHERE session = ? AND closed IS NULL ORDER BY made",
                                               session_id)]

    def give_tab(self, tab_id, session_id):
        self._run("UPDATE tabs SET session = ? WHERE id = ?", session_id, tab_id)

    def close_tab(self, tab_id, when):
        self._run("UPDATE tabs SET closed = ? WHERE id = ? AND closed IS NULL", when, tab_id)

    def close_all(self, when):
        """Close every open session and tab, as when every Chrome quits with the server."""
        with self._lock:
            self._db.execute("UPDATE sessions SET closed = ? WHERE closed IS NULL", (when,))
            self._db.execute("UPDATE tabs SET closed = ? WHERE closed IS NULL", (when,))

    def close(self):
        with self._lock:
            self._db.close()
