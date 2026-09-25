#!/usr/bin/env python3
"""Zero-restart sidecar that records a live karaoke night for later fixture building.

Runs alongside kj-controller WITHOUT touching it (no restart, no code change in
the app process). It captures three timelines into one output directory:

  db_changes.jsonl  Row-level diffs (insert / update / delete) of every table in
                    rotation.db and media_library.db, detected via
                    ``PRAGMA data_version`` so idle ticks cost ~nothing.
  status.jsonl      /status snapshots (playback state, current song, volumes,
                    pitch, downloads...) written whenever anything other than the
                    playback clock changes, plus a heartbeat every 30s.
  journal.jsonl     ``journalctl -u kj-controller -o json -f`` — every access-log
                    line (method, path, query string, status) and app log line.
  snapshots/        Full SQLite backups at start, every N minutes, and at exit.

Stdlib only, so it runs with the device's system python. Output contains REAL
phone numbers / names — keep it on the device or a private machine; redact with
``scripts/night_fixture.py`` before anything is committed.

Usage on the device (as a transient unit, survives SSH disconnects):

  sudo systemd-run --unit=kj-night-capture --uid=nomad --gid=nomad \
      --property=Nice=10 /usr/bin/python3 /home/nomad/kjdata/night-capture/night_capture.py \
      --out /home/nomad/kjdata/night-captures/$(date +%F)

Stop with ``sudo systemctl stop kj-night-capture`` (takes a final snapshot).
"""

import argparse
import base64
import datetime as dt
import gzip
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

DEFAULT_DBS = {
    'rotation': '/home/nomad/kjdata/rotation.db',
    'media': '/opt/nomad/data/media_library.db',
}
# Keys in /status that change every tick and would make every sample "new".
VOLATILE_STATUS_KEYS = {'time'}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def jsonable(value):
    if isinstance(value, (bytes, memoryview)):
        raw = bytes(value)
        if len(raw) > 4096:
            return {'__blob_len__': len(raw)}
        return {'__b64__': base64.b64encode(raw).decode()}
    return value


class JsonlWriter:
    def __init__(self, path):
        self._fh = open(path, 'a', buffering=1, encoding='utf-8')
        self._lock = threading.Lock()

    def write(self, record):
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + '\n')

    def close(self):
        with self._lock:
            self._fh.close()


class DbWatcher:
    """Diffs every table of one SQLite DB whenever another connection commits."""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True,
                                    isolation_level=None, timeout=5)
        self.last_version = None
        self.state = {}  # table -> {rowid: row_dict}

    def _tables(self):
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
        return [r[0] for r in rows]

    def _read_table(self, table):
        cur = self.conn.execute(f'SELECT rowid AS __rowid__, * FROM [{table}]')
        cols = [d[0] for d in cur.description]
        out = {}
        for row in cur.fetchall():
            rec = {c: jsonable(v) for c, v in zip(cols, row)}
            out[rec.pop('__rowid__')] = rec
        return out

    def poll(self, emit, initial=False):
        version = self.conn.execute('PRAGMA data_version').fetchone()[0]
        if not initial and version == self.last_version:
            return 0
        self.last_version = version
        changes = 0
        ts = now_iso()
        for table in self._tables():
            new = self._read_table(table)
            old = self.state.get(table)
            self.state[table] = new
            if initial or old is None:
                continue
            for rowid, row in new.items():
                prev = old.get(rowid)
                if prev is None:
                    emit({'ts': ts, 'db': self.name, 'table': table,
                          'op': 'insert', 'rowid': rowid, 'row': row})
                    changes += 1
                elif prev != row:
                    diff = {k: [prev.get(k), v] for k, v in row.items()
                            if prev.get(k) != v}
                    emit({'ts': ts, 'db': self.name, 'table': table,
                          'op': 'update', 'rowid': rowid, 'changed': diff})
                    changes += 1
            for rowid in old.keys() - new.keys():
                emit({'ts': ts, 'db': self.name, 'table': table,
                      'op': 'delete', 'rowid': rowid, 'row': old[rowid]})
                changes += 1
        return changes

    def snapshot(self, dest):
        """Consistent online backup (does not block the app's writers for long)."""
        tmp = dest + '.tmp'
        target = sqlite3.connect(tmp)
        with target:
            self.conn.backup(target, pages=256, sleep=0.01)
        target.close()
        with open(tmp, 'rb') as src, gzip.open(dest + '.gz', 'wb', 6) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(tmp)


def fetch_status(url):
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.loads(resp.read().decode())


def journal_follower(out_path, stop_evt, units):
    cmd = ['journalctl', '-o', 'json', '-f', '--since', 'now', '--no-pager']
    for unit in units:
        cmd += ['-u', unit]
    with open(out_path, 'ab') as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.DEVNULL)
        stop_evt.wait()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', required=True)
    ap.add_argument('--status-url', default='http://127.0.0.1:5001/status')
    ap.add_argument('--db', action='append', default=[],
                    help='name=path (repeatable); defaults to rotation + media DBs')
    ap.add_argument('--db-interval', type=float, default=1.0)
    ap.add_argument('--status-interval', type=float, default=1.0)
    ap.add_argument('--heartbeat', type=float, default=30.0)
    ap.add_argument('--snapshot-minutes', type=float, default=15.0)
    ap.add_argument('--journal-unit', action='append', default=[])
    ap.add_argument('--no-journal', action='store_true')
    args = ap.parse_args()

    dbs = dict(DEFAULT_DBS)
    if args.db:
        dbs = dict(item.split('=', 1) for item in args.db)
    units = args.journal_unit or ['kj-controller']

    os.makedirs(os.path.join(args.out, 'snapshots'), exist_ok=True)
    stop_evt = threading.Event()

    def _stop(*_):
        stop_evt.set()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    changes_w = JsonlWriter(os.path.join(args.out, 'db_changes.jsonl'))
    status_w = JsonlWriter(os.path.join(args.out, 'status.jsonl'))
    meta_w = JsonlWriter(os.path.join(args.out, 'capture_log.jsonl'))
    meta_w.write({'ts': now_iso(), 'event': 'start', 'argv': sys.argv,
                  'dbs': dbs, 'pid': os.getpid()})

    watchers = [DbWatcher(name, path) for name, path in dbs.items()]

    def snapshot_all(label):
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        for w in watchers:
            dest = os.path.join(args.out, 'snapshots', f'{label}-{stamp}-{w.name}.db')
            try:
                w.snapshot(dest)
            except Exception as exc:  # keep capturing even if a backup fails
                meta_w.write({'ts': now_iso(), 'event': 'snapshot_error',
                              'db': w.name, 'error': repr(exc)})
        meta_w.write({'ts': now_iso(), 'event': 'snapshot', 'label': label})

    snapshot_all('start')
    for w in watchers:
        w.poll(changes_w.write, initial=True)

    journal_thread = None
    if not args.no_journal:
        journal_thread = threading.Thread(
            target=journal_follower, daemon=True,
            args=(os.path.join(args.out, 'journal.jsonl'), stop_evt, units))
        journal_thread.start()

    last_sig = None
    last_status_write = 0.0
    last_snapshot = time.monotonic()
    next_db = next_status = 0.0
    status_errors = 0
    while not stop_evt.is_set():
        mono = time.monotonic()
        if mono >= next_db:
            next_db = mono + args.db_interval
            for w in watchers:
                try:
                    w.poll(changes_w.write)
                except sqlite3.Error as exc:
                    meta_w.write({'ts': now_iso(), 'event': 'db_error',
                                  'db': w.name, 'error': repr(exc)})
        if mono >= next_status:
            next_status = mono + args.status_interval
            try:
                status = fetch_status(args.status_url)
                sig = json.dumps({k: v for k, v in status.items()
                                  if k not in VOLATILE_STATUS_KEYS}, sort_keys=True)
                if sig != last_sig or mono - last_status_write >= args.heartbeat:
                    status_w.write({'ts': now_iso(), 'changed': sig != last_sig,
                                    'status': status})
                    last_sig = sig
                    last_status_write = mono
                status_errors = 0
            except Exception as exc:
                status_errors += 1
                if status_errors in (1, 10, 100) or status_errors % 600 == 0:
                    meta_w.write({'ts': now_iso(), 'event': 'status_error',
                                  'count': status_errors, 'error': repr(exc)})
        if mono - last_snapshot >= args.snapshot_minutes * 60:
            last_snapshot = mono
            snapshot_all('periodic')
        stop_evt.wait(0.2)

    snapshot_all('final')
    meta_w.write({'ts': now_iso(), 'event': 'stop'})
    if journal_thread:
        journal_thread.join(timeout=10)
    for w in (changes_w, status_w, meta_w):
        w.close()


if __name__ == '__main__':
    main()
