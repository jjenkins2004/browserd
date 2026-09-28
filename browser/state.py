"""browserd's own records, in one SQLite file, .run/state.db: the profiles, the sessions, their tabs and which tabs
need the user's input.

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
CREATE TABLE IF NOT EXISTS needs_input (
    tab TEXT PRIMARY KEY,
    note TEXT NOT NULL,
    since REAL NOT NULL
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

    def remove_profile(self, name):
        """Remove the profile of that name, whatever its case; its sessions and tabs are kept."""
        self._run("DELETE FROM profiles WHERE name = ?", name)

    def add_session(self, session):
        """Add a session, or raise sqlite3.IntegrityError when its id is taken."""
        self._run("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)", *session)

    def session(self, session_id):
        rows = self._all("SELECT * FROM sessions WHERE id = ?", session_id)
        return Session(*rows[0]) if rows else None

    def open_sessions(self):
        """Every session not closed, oldest first."""
        return [Session(*row) for row in self._all("SELECT * FROM sessions WHERE closed IS NULL ORDER BY started")]

    def closed_sessions(self, profile, most):
        """A profile's most recently closed sessions, newest first."""
        return [Session(*row) for row in self._all("SELECT * FROM sessions WHERE profile = ? AND closed IS NOT NULL "
                                                   "ORDER BY closed DESC LIMIT ?", profile, most)]

    def close_session(self, session_id, when):
        self._run("UPDATE sessions SET closed = ? WHERE id = ? AND closed IS NULL", when, session_id)

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
        with self._lock:
            self._db.execute("UPDATE tabs SET closed = ? WHERE id = ? AND closed IS NULL", (when, tab_id))
            self._db.execute("DELETE FROM needs_input WHERE tab = ?", (tab_id,))

    def close_all(self, when):
        """Close every open session and tab, as when every Chrome quits with the server."""
        with self._lock:
            self._db.execute("UPDATE sessions SET closed = ? WHERE closed IS NULL", (when,))
            self._db.execute("UPDATE tabs SET closed = ? WHERE closed IS NULL", (when,))
            self._db.execute("DELETE FROM needs_input")

    def mark_needs_input(self, tab_id, note, when):
        """Mark an open tab as needing the user's input; marked already, it takes the new note and keeps its since. A
        tab closed meanwhile gets no mark."""
        self._run("INSERT INTO needs_input SELECT ?, ?, ? FROM tabs WHERE id = ? AND closed IS NULL "
                  "ON CONFLICT (tab) DO UPDATE SET note = excluded.note", tab_id, note, when, tab_id)

    def clear_needs_input(self, tab_id):
        self._run("DELETE FROM needs_input WHERE tab = ?", tab_id)

    def needs_input(self):
        """Every tab marked as needing the user's input, as tab id -> (note, since)."""
        return {tab: (note, since) for tab, note, since in self._all("SELECT tab, note, since FROM needs_input")}

    def close(self):
        with self._lock:
            self._db.close()
