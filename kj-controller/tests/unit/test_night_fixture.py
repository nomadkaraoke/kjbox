"""night_fixture: recorded nights become fixtures with no real phone numbers / IPs / push secrets."""

import gzip
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
import night_fixture  # noqa: E402

REAL = '+14045551234'


def _make_capture(tmp_path):
    cap = tmp_path / 'capture'
    (cap / 'snapshots').mkdir(parents=True)
    db = tmp_path / 'rot.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE sing_requests (id INTEGER PRIMARY KEY, singer_name TEXT, phone TEXT, meta TEXT)')
    conn.execute('CREATE TABLE sing_push_subscriptions (id INTEGER PRIMARY KEY, phone TEXT, endpoint TEXT, p256dh TEXT, auth TEXT)')
    conn.execute('INSERT INTO sing_requests VALUES (1, ?, ?, ?)',
                 ('Alice', REAL, json.dumps({'note': 'call me on (404) 555-1234'})))
    conn.execute('INSERT INTO sing_push_subscriptions VALUES (1, ?, ?, ?, ?)',
                 (REAL, 'https://fcm.googleapis.com/secret', 'pkey', 'akey'))
    conn.commit()
    conn.close()
    with open(db, 'rb') as src, gzip.open(cap / 'snapshots' / 'start-20260924-210000-rotation.db.gz', 'wb') as dst:
        dst.write(src.read())
    (cap / 'db_changes.jsonl').write_text(json.dumps({
        'table': 'sms_log', 'op': 'insert', 'row': {'phone_e164': REAL, 'body': 'Hi 404.555.1234, you are up!'}}) + '\n')
    (cap / 'status.jsonl').write_text(json.dumps({'status': {'state': 'playing'}}) + '\n')
    (cap / 'journal.jsonl').write_text(json.dumps({
        '__REALTIME_TIMESTAMP': '1', '_PID': '9', 'MESSAGE': f'SMS sent to {REAL}'}) + '\n')
    actions = tmp_path / 'actions.jsonl'
    actions.write_text(json.dumps({
        'actor': 'singer', 'path': '/sing/submit', 'endpoint': 'sing.submit',
        'body': {'singer_name': 'Alice', 'phone': '404-555-1234'},
        'client': {'CF-Connecting-IP': '203.0.113.9'}, 'session_id': 'abc'}) + '\n')
    return cap, actions


def test_build_removes_every_real_phone(tmp_path):
    cap, actions = _make_capture(tmp_path)
    out = tmp_path / 'fixture'
    summary, _ = night_fixture.build(str(cap), str(actions), str(out))
    assert summary['distinct_phones'] == 1
    blob = ''
    for root, _, files in os.walk(out):
        for f in files:
            blob += open(os.path.join(root, f), 'rb').read().decode('utf-8', 'ignore')
    digits = ''.join(ch for ch in blob if ch.isdigit() or ch == '\n')
    assert '4045551234' not in digits
    assert '203.0.113.9' not in blob

    act = json.loads((out / 'actions.jsonl').read_text())
    assert act['body']['phone'] == '+15550000001'
    assert act['body']['singer_name'] == 'Alice'  # names kept by default
    assert act['endpoint'] == 'sing.submit'  # Flask route names are not secrets
    assert act['client']['CF-Connecting-IP'].startswith('10.')
    change = json.loads((out / 'db_changes.jsonl').read_text())
    assert change['row']['body'] == 'Hi +15550000001, you are up!'

    conn = sqlite3.connect(out / 'snapshots' / 'start-rotation.db')
    phone, meta = conn.execute('SELECT phone, meta FROM sing_requests').fetchone()
    assert phone == '+15550000001'
    assert '+15550000001' in meta
    endpoint, p256dh = conn.execute('SELECT endpoint, p256dh FROM sing_push_subscriptions').fetchone()
    assert endpoint.startswith('redacted-') and p256dh.startswith('redacted-')


def test_pseudonymize_names(tmp_path):
    cap, actions = _make_capture(tmp_path)
    out = tmp_path / 'fixture'
    night_fixture.build(str(cap), str(actions), str(out), pseudonymize_names=True)
    act = json.loads((out / 'actions.jsonl').read_text())
    assert act['body']['singer_name'] == 'Singer001'
    conn = sqlite3.connect(out / 'snapshots' / 'start-rotation.db')
    assert conn.execute('SELECT singer_name FROM sing_requests').fetchone()[0] == 'Singer001'


def test_short_digit_runs_untouched():
    red = night_fixture.Redactor()
    red.add_phone(REAL)
    assert red.scrub_text('rowid 1234 at 20260924') == 'rowid 1234 at 20260924'
