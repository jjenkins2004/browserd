"""browserd's own records, in one SQLite file, .run/state.db: the profiles.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import sqlite3
import threading

from .profiles import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    folder TEXT NOT NULL UNIQUE COLLATE NOCASE,
    port INTEGER NOT NULL UNIQUE
);
"""


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

    def profiles(self):
        """Every profile, by name."""
        with self._lock:
            rows = self._db.execute("SELECT name, folder, port FROM profiles ORDER BY name").fetchall()
        return [Profile(*row) for row in rows]

    def add_profile(self, profile):
        """Add a profile, or raise sqlite3.IntegrityError when its name, folder or port is taken."""
        with self._lock:
            self._db.execute("INSERT INTO profiles (name, folder, port) VALUES (?, ?, ?)", tuple(profile))

    def close(self):
        with self._lock:
            self._db.close()
