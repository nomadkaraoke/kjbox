#!/usr/bin/env python3
"""Turn a recorded karaoke night into a redacted replay fixture.

Inputs (copied off NomadPC after the show — see docs/NIGHT-RECORDING.md):
  --capture DIR      sidecar output (db_changes.jsonl, status.jsonl, journal.jsonl,
                     snapshots/*.db.gz)
  --actions FILE     ActionRecorder log (~/kjdata/action-logs/<night>.jsonl), optional

Output (--out DIR):
  actions.jsonl      redacted ActionRecorder log
  db_changes.jsonl   redacted row diffs
  status.jsonl       /status timeline (no PII, copied through the same scrubber)
  journal.jsonl      slimmed journal: {ts, pid, message}
  snapshots/*.db     redacted SQLite snapshots (start / final)
  redaction.json     counts only (the real→fake map is NEVER written)

Redaction:
  - Phone numbers → stable fakes (+1555xxxxxxx, same real number ⇒ same fake), found
    by harvesting every phone-ish column / body key first, then replacing those
    digit sequences in ANY format anywhere (free text, SMS bodies, webhooks).
  - Client IPs → stable 10.x.y.z fakes.
  - Web-push endpoint / p256dh / auth, Telnyx message ids, session hashes → hashed.
  - Names are kept unless --pseudonymize-names (repo is PUBLIC — decide per use).
"""

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys

PHONE_KEYS = {'phone', 'phone_e164', 'to', 'from', 'phone_number', 'new_phone'}
SECRET_KEYS = {'endpoint', 'p256dh', 'auth', 'telnyx_message_id', 'edit_token',
               'session_id', 'vapid_private_key'}
IP_KEYS = {'CF-Connecting-IP', 'X-Forwarded-For', 'ip', 'remote_addr'}
NAME_KEYS = {'singer', 'singer_name', 'name', 'new_name', 'canonical_name', 'display_name'}
LOOSE_PHONE_RE = re.compile(r'\+?\d[\d\s().-]{8,}\d')


def _digits(s):
    return re.sub(r'\D', '', s or '')


def _key10(s):
    d = _digits(s)
    return d[-10:] if len(d) >= 10 else None


def _h(value, n=10):
    return hashlib.sha256(str(value).encode()).hexdigest()[:n]


class Redactor:
    def __init__(self, pseudonymize_names=False):
        self.phone_map = {}      # last-10-digits -> fake E.164
        self.ip_map = {}
        self.name_map = {}
        self.pseudonymize_names = pseudonymize_names
        self.counts = {'phones_replaced': 0, 'ips': 0, 'secrets': 0, 'names': 0}
        self._phone_re = None

    # -- harvesting -------------------------------------------------------
    def add_phone(self, raw):
        if not isinstance(raw, str):
            return
        k = _key10(raw)
        if k and k not in self.phone_map:
            self.phone_map[k] = f'+1555{len(self.phone_map) + 1:07d}'
            self._phone_re = None

    def harvest(self, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in PHONE_KEYS and isinstance(v, str) and len(_digits(v)) >= 10:
                    self.add_phone(v)
                self.harvest(v)
        elif isinstance(obj, list):
            for v in obj:
                self.harvest(v)

    def harvest_db(self, path):
        conn = sqlite3.connect(path)
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info([{table}])')]
            for col in cols:
                if 'phone' in col.lower():
                    for (v,) in conn.execute(f'SELECT [{col}] FROM [{table}]'):
                        self.add_phone(v)
        conn.close()

    # -- replacing --------------------------------------------------------
    def _compiled(self):
        if self._phone_re is None and self.phone_map:
            # digits of the last 10, allowing any separators between them,
            # optionally preceded by +1 / 1.
            alts = ['[\\s().-]*'.join(k) for k in self.phone_map]
            self._phone_re = re.compile(
                r'(?:\+?1[\s().-]*)?(?:' + '|'.join(alts) + r')(?!\d)')
        return self._phone_re

    def scrub_text(self, s):
        rx = self._compiled()
        if not rx or not LOOSE_PHONE_RE.search(s):
            return s

        def sub(m):
            self.counts['phones_replaced'] += 1
            return self.phone_map[_key10(m.group(0))]
        return rx.sub(sub, s)

    def fake_ip(self, ip):
        if ip not in self.ip_map:
            n = len(self.ip_map) + 1
            self.ip_map[ip] = f'10.{n // 65536 % 256}.{n // 256 % 256}.{n % 256}'
        self.counts['ips'] += 1
        return self.ip_map[ip]

    def fake_name(self, name):
        key = name.strip().lower()
        if key not in self.name_map:
            self.name_map[key] = f'Singer{len(self.name_map) + 1:03d}'
        self.counts['names'] += 1
        return self.name_map[key]

    def scrub(self, obj, key=None):
        if isinstance(obj, dict):
            return {k: self.scrub(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub(v, key) for v in obj]
        if not isinstance(obj, str):
            return obj
        # 'endpoint' is both a web-push URL (secret) and a Flask route name (keep).
        if key in SECRET_KEYS and obj and (key != 'endpoint' or obj.startswith('http')):
            self.counts['secrets'] += 1
            return f'redacted-{_h(obj)}'
        if key in IP_KEYS and obj:
            return ', '.join(self.fake_ip(p.strip()) for p in obj.split(','))
        if self.pseudonymize_names and key in NAME_KEYS and obj.strip():
            return self.fake_name(obj)
        return self.scrub_text(obj)

    def scrub_db(self, src, dest):
        shutil.copyfile(src, dest)
        conn = sqlite3.connect(dest)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info([{table}])')]
            rows = conn.execute(f'SELECT rowid, * FROM [{table}]').fetchall()
            for row in rows:
                rowid, values = row[0], row[1:]
                new = []
                for col, v in zip(cols, values):
                    if isinstance(v, str):
                        if v[:1] in '{[':
                            try:
                                v = json.dumps(self.scrub(json.loads(v)), ensure_ascii=False)
                            except ValueError:
                                v = self.scrub(v, col)
                        else:
                            v = self.scrub(v, col)
                    new.append(v)
                if list(new) != list(values):
                    sets = ', '.join(f'[{c}] = ?' for c in cols)
                    conn.execute(f'UPDATE [{table}] SET {sets} WHERE rowid = ?',
                                 (*new, rowid))
        conn.commit()
        conn.execute('VACUUM')
        conn.close()


def _read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_jsonl(path, records):
    with open(path, 'w', encoding='utf-8') as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')


def _gunzip_to(src, dest):
    with gzip.open(src, 'rb') as fin, open(dest, 'wb') as fout:
        shutil.copyfileobj(fin, fout)


def build(capture, actions_path, out, pseudonymize_names=False, snapshot_labels=('start', 'final')):
    os.makedirs(os.path.join(out, 'snapshots'), exist_ok=True)
    tmp = os.path.join(out, '.raw')
    os.makedirs(tmp, exist_ok=True)
    red = Redactor(pseudonymize_names=pseudonymize_names)

    actions = _read_jsonl(actions_path)
    changes = _read_jsonl(os.path.join(capture, 'db_changes.jsonl'))
    status = _read_jsonl(os.path.join(capture, 'status.jsonl'))
    journal = _read_jsonl(os.path.join(capture, 'journal.jsonl'))

    # Pick first snapshot per label per DB (start) / last (final).
    raw_dbs = []
    for label in snapshot_labels:
        found = sorted(glob.glob(os.path.join(capture, 'snapshots', f'{label}-*.db.gz')))
        by_db = {}
        for p in found:
            db = os.path.basename(p).rsplit('-', 1)[1].split('.')[0]
            if label == 'start':
                by_db.setdefault(db, p)
            else:
                by_db[db] = p
        for db, p in by_db.items():
            raw = os.path.join(tmp, f'{label}-{db}.db')
            _gunzip_to(p, raw)
            raw_dbs.append((label, db, raw))

    # 1) harvest every phone we can see, 2) scrub everything.
    for _, _, raw in raw_dbs:
        red.harvest_db(raw)
    for rec in actions + changes:
        red.harvest(rec)

    _write_jsonl(os.path.join(out, 'actions.jsonl'), [red.scrub(r) for r in actions])
    _write_jsonl(os.path.join(out, 'db_changes.jsonl'), [red.scrub(r) for r in changes])
    _write_jsonl(os.path.join(out, 'status.jsonl'), [red.scrub(r) for r in status])
    slim = [{'ts_us': int(j.get('__REALTIME_TIMESTAMP', 0)), 'pid': j.get('_PID'),
             'message': red.scrub_text(j['MESSAGE']) if isinstance(j.get('MESSAGE'), str)
             else j.get('MESSAGE')} for j in journal]
    _write_jsonl(os.path.join(out, 'journal.jsonl'), slim)
    for label, db, raw in raw_dbs:
        red.scrub_db(raw, os.path.join(out, 'snapshots', f'{label}-{db}.db'))
    shutil.rmtree(tmp)

    summary = {
        'actions': len(actions), 'db_changes': len(changes), 'status_samples': len(status),
        'journal_lines': len(journal), 'snapshots': [f'{l}-{d}' for l, d, _ in raw_dbs],
        'distinct_phones': len(red.phone_map), 'distinct_ips': len(red.ip_map),
        'names_pseudonymized': pseudonymize_names, **red.counts,
    }
    with open(os.path.join(out, 'redaction.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    return summary, red


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--capture', required=True)
    ap.add_argument('--actions')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pseudonymize-names', action='store_true')
    args = ap.parse_args()
    summary, _ = build(args.capture, args.actions, args.out, args.pseudonymize_names)
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
