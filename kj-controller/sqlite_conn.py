"""Per-thread SQLite connections for read-mostly stores shared across Flask threads.

A single ``sqlite3.Connection`` with ``check_same_thread=False`` is NOT safe for
concurrent use: two request threads executing on it at once can clobber each
other's statement state, so ``fetchone()`` intermittently returns ``None`` for a
``SELECT COUNT(*)`` (seen live as ``TypeError: 'NoneType' object is not
subscriptable`` in ``/sing/search``). Each thread gets its own connection here.

``reset()`` bumps a generation counter and closes every connection opened so
far; threads lazily reopen on their next ``get()`` (e.g. after the DB file was
replaced on disk).
"""

import sqlite3
import threading


class ThreadLocalConnection:
    def __init__(self, db_path, pragmas=()):
        self.db_path = db_path
        self._pragmas = tuple(pragmas)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._generation = 0
        self._all = []

    def get(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._local.generation == self._generation:
            return conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for pragma in self._pragmas:
            conn.execute(f"PRAGMA {pragma}")
        with self._lock:
            self._all.append(conn)
            self._local.conn = conn
            self._local.generation = self._generation
        return conn

    def current(self):
        """This thread's live connection, or None (no connect side effect)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._local.generation == self._generation:
            return conn
        return None

    def reset(self):
        with self._lock:
            self._generation += 1
            conns, self._all = self._all, []
        self._local.conn = None
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
