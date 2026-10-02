"""Per-thread SQLite connections for read-mostly stores shared across Flask threads.

A single ``sqlite3.Connection`` with ``check_same_thread=False`` is NOT safe for
concurrent use: two request threads executing on it at once can clobber each
other's statement state, so ``fetchone()`` intermittently returns ``None`` for a
``SELECT COUNT(*)`` (seen live as ``TypeError: 'NoneType' object is not
subscriptable`` in ``/sing/search``). Each thread gets its own connection here.

``reset()`` bumps a generation counter (e.g. after the DB file was replaced on
disk). Each thread closes its own stale connection on its next ``get()``, so a
connection is never closed while another thread is mid-query on it. The
calling thread's connection is closed immediately.
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

    def get(self):
        conn = self.current()
        if conn is not None:
            return conn
        self._drop_local()
        while True:
            generation = self._generation
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            for pragma in self._pragmas:
                conn.execute(f"PRAGMA {pragma}")
            with self._lock:
                if generation == self._generation:
                    self._local.conn = conn
                    self._local.generation = generation
                    return conn
            # reset() ran while we were opening: this may be the old file.
            conn.close()

    def current(self):
        """This thread's live connection, or None (no connect side effect)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._local.generation == self._generation:
            return conn
        return None

    def reset(self):
        with self._lock:
            self._generation += 1
        self._drop_local()

    def _drop_local(self):
        conn = getattr(self._local, "conn", None)
        self._local.conn = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
