"""ActionRecorder — append every meaningful KJ / singer HTTP action to a per-night JSONL.

Purpose: capture real karaoke nights (request bodies, responses, which device did
what, and when) so they can be turned into realistic replay fixtures. See
docs/NIGHT-RECORDING.md.

Design rules:
- Never break a request: every hook swallows its own errors.
- Skip the high-frequency polls (/status, GET /rotation, ...) — the sidecar
  ``scripts/night_capture.py`` already records the state they return.
- Record raw values (real names / phones). Redaction happens when fixtures are
  built, never on the device, so nothing is lost at capture time.
"""

import datetime as dt
import hashlib
import json
import os
import threading
import time

from flask import g, request

MAX_BODY_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024

# GET endpoints polled every few seconds by the KJ UI / singer SPA. Their
# responses are state reads, captured by the sidecar instead.
DEFAULT_SKIP_GET_PATHS = frozenset({
    '/status', '/rotation', '/system/stats', '/rotation/requests',
    '/rotation/sync-status', '/perf/stream',
    '/favicon.ico', '/sw.js', '/sing/sw.js', '/sing/manifest.webmanifest',
    '/manifest.webmanifest',
})
DEFAULT_SKIP_PREFIXES = ('/static/', '/sing/static/', '/sing/lib/', '/vnc', '/websockify',
                         # Preview media segments: one request per HLS/CDG chunk.
                         '/sing/preview/hls/', '/sing/preview/cdg/',
                         '/sing/preview/stream/', '/preview/hls/', '/preview/cdg/',
                         '/preview/stream/')

# Header / cookie values that identify a client without being secrets we
# need to keep verbatim.
_CLIENT_HEADERS = ('CF-Connecting-IP', 'X-Forwarded-For', 'User-Agent',
                   'Accept-Language', 'Referer')


def night_date(now=None):
    """Date a karaoke night belongs to: before noon counts as the previous night."""
    now = now or dt.datetime.now()
    return (now - dt.timedelta(hours=12)).date().isoformat()


def _truncate_json(value, limit):
    raw = json.dumps(value, default=str, ensure_ascii=False)
    if len(raw) <= limit:
        return value
    return {'__truncated__': True, 'bytes': len(raw),
            'sha1': hashlib.sha1(raw.encode()).hexdigest(), 'head': raw[:2048]}


def _short_hash(value):
    return hashlib.sha1(value.encode()).hexdigest()[:12] if value else None


class ActionRecorder:
    def __init__(self, log_dir, skip_get_paths=DEFAULT_SKIP_GET_PATHS,
                 skip_prefixes=DEFAULT_SKIP_PREFIXES, clock=None):
        self.log_dir = log_dir
        self.skip_get_paths = set(skip_get_paths)
        self.skip_prefixes = tuple(skip_prefixes)
        self._clock = clock or dt.datetime.now
        self._lock = threading.Lock()
        self._seq = 0
        os.makedirs(log_dir, exist_ok=True)

    def path_for(self, now=None):
        return os.path.join(self.log_dir, f'{night_date(now or self._clock())}.jsonl')

    def should_record(self, method, path):
        if path.startswith(self.skip_prefixes):
            return False
        if method in ('GET', 'HEAD', 'OPTIONS') and path in self.skip_get_paths:
            return False
        return method != 'OPTIONS'

    def write(self, record):
        now = self._clock()
        with self._lock:
            self._seq += 1
            record['seq'] = self._seq
            line = json.dumps(record, default=str, ensure_ascii=False)
            with open(self.path_for(now), 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')


def _request_body():
    if request.is_json:
        body = request.get_json(silent=True)
        if body is None and request.content_length:
            return {'__unparsed__': request.get_data(as_text=True)[:MAX_BODY_BYTES]}
        return _truncate_json(body, MAX_BODY_BYTES)
    if request.form or request.files:
        body = {k: request.form.getlist(k) if len(request.form.getlist(k)) > 1
                else request.form.get(k) for k in request.form}
        files = {k: {'filename': f.filename, 'content_type': f.content_type}
                 for k, f in request.files.items()}
        if files:
            body['__files__'] = files
        return _truncate_json(body, MAX_BODY_BYTES)
    if request.content_length:
        return {'__raw_bytes__': request.content_length}
    return None


def _response_body(resp):
    if resp.direct_passthrough or resp.is_streamed:
        return {'__streamed__': True}
    ctype = resp.mimetype or ''
    if ctype != 'application/json':
        return {'__content_type__': ctype, 'bytes': resp.calculate_content_length()}
    try:
        return _truncate_json(json.loads(resp.get_data(as_text=True)), MAX_RESPONSE_BYTES)
    except (ValueError, UnicodeDecodeError):
        return {'__unparsed_json__': True}


def install_action_recorder(flask_app, log_dir):
    """Attach before/after/teardown hooks that append to ``<log_dir>/<night>.jsonl``."""
    recorder = ActionRecorder(log_dir)
    flask_app.action_recorder = recorder

    @flask_app.before_request
    def _action_rec_start():
        try:
            g._action_rec = recorder.should_record(request.method, request.path)
            if g._action_rec:
                g._action_rec_t0 = time.perf_counter()
                g._action_rec_ts = dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec='milliseconds')
        except Exception:
            g._action_rec = False

    def _base_record():
        headers = {h: request.headers.get(h) for h in _CLIENT_HEADERS
                   if request.headers.get(h)}
        return {
            'ts': g._action_rec_ts,
            'actor': 'singer' if (request.endpoint or '').startswith('sing.') else 'kj',
            'method': request.method,
            'host': request.host,
            'path': request.path,
            'endpoint': request.endpoint,
            'view_args': request.view_args or None,
            'query': request.args.to_dict(flat=False) or None,
            'body': _request_body(),
            'client': headers,
            'session_id': _short_hash(request.cookies.get('session', '')),
        }

    @flask_app.after_request
    def _action_rec_finish(resp):
        try:
            if getattr(g, '_action_rec', False):
                g._action_rec = False  # teardown must not double-log
                rec = _base_record()
                rec.update({
                    'status': resp.status_code,
                    'duration_ms': round((time.perf_counter() - g._action_rec_t0) * 1000, 1),
                    'response': _response_body(resp),
                })
                recorder.write(rec)
        except Exception:
            pass
        return resp

    @flask_app.teardown_request
    def _action_rec_error(exc):
        # Unhandled exceptions skip after_request; still record the attempt.
        try:
            if exc is not None and getattr(g, '_action_rec', False):
                g._action_rec = False
                rec = _base_record()
                rec.update({'status': 500, 'error': repr(exc)[:2000],
                            'duration_ms': round(
                                (time.perf_counter() - g._action_rec_t0) * 1000, 1)})
                recorder.write(rec)
        except Exception:
            pass

    return recorder
