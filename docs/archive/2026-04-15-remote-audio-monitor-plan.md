# Remote Audio Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable remote audio monitoring of NomadPC karaoke playback by streaming live audio over HTTP.

**Architecture:** Toggle mode — enabling the monitor switches audio from ALSA-direct to PipeWire, starts an ffmpeg capture from the PipeWire monitor source, and serves the MP3 stream via a Flask route. Disabling reverses everything. State is not persisted.

**Tech Stack:** Python/Flask, ffmpeg, PipeWire (pactl/pw-record), subprocess management

**Spec:** `docs/archive/2026-04-15-remote-audio-monitor-design.md`

---

### Task 1: Add audio_backend to MpvManager

**Files:**
- Modify: `kj-controller/mpv_manager.py`
- Test: `kj-controller/tests/unit/test_mpv_manager.py`

MpvManager needs an `audio_backend` property (`'alsa'` or `'pipewire'`) that controls how mpv and VLC are launched.

- [ ] **Step 1: Write failing tests for audio_backend property**

Add to `tests/unit/test_mpv_manager.py`:

```python
# --- Audio backend tests ---

def test_audio_backend_defaults_to_alsa(mock_config):
    m = MpvManager(mock_config, enabled=False)
    assert m.audio_backend == 'alsa'


def test_audio_backend_stored(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.audio_backend = 'pipewire'
    assert m.audio_backend == 'pipewire'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_mpv_manager.py::test_audio_backend_defaults_to_alsa tests/unit/test_mpv_manager.py::test_audio_backend_stored -v`
Expected: FAIL with `AttributeError: 'MpvManager' object has no attribute 'audio_backend'`

- [ ] **Step 3: Add audio_backend property to MpvManager.__init__**

In `kj-controller/mpv_manager.py`, add to `__init__` after `self.audio_device = ...` (line 37):

```python
        self.audio_backend = 'alsa'  # 'alsa' (direct) or 'pipewire'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_mpv_manager.py::test_audio_backend_defaults_to_alsa tests/unit/test_mpv_manager.py::test_audio_backend_stored -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for PipeWire launch commands**

Add to `tests/unit/test_mpv_manager.py`:

```python
def test_mpv_launch_alsa_command(mock_config, mocker):
    """When audio_backend='alsa', mpv uses --ao=alsa --audio-device=alsa/hdmiout."""
    m = MpvManager(mock_config, enabled=True)
    m.audio_backend = 'alsa'
    mock_popen = mocker.patch('mpv_manager.subprocess.Popen')
    mocker.patch('builtins.open', mocker.mock_open())
    mocker.patch('mpv_manager.os.path.exists', return_value=False)
    mocker.patch('mpv_manager.os.unlink', side_effect=FileNotFoundError)

    m._launch_mpv_karaoke()

    args = mock_popen.call_args[0][0]
    assert '--ao=alsa' in args
    assert f'--audio-device=alsa/{m.audio_device}' in args


def test_mpv_launch_pipewire_command(mock_config, mocker):
    """When audio_backend='pipewire', mpv uses --ao=pipewire with no --audio-device."""
    m = MpvManager(mock_config, enabled=True)
    m.audio_backend = 'pipewire'
    mock_popen = mocker.patch('mpv_manager.subprocess.Popen')
    mocker.patch('builtins.open', mocker.mock_open())
    mocker.patch('mpv_manager.os.path.exists', return_value=False)
    mocker.patch('mpv_manager.os.unlink', side_effect=FileNotFoundError)

    m._launch_mpv_karaoke()

    args = mock_popen.call_args[0][0]
    assert '--ao=pipewire' in args
    assert not any(a.startswith('--audio-device=') for a in args)


def test_vlc_filler_launch_alsa_command(mock_config, mocker):
    """When audio_backend='alsa', VLC filler uses --aout alsa --alsa-audio-device."""
    mocker.patch('mpv_manager.is_pi', return_value=False)
    m = MpvManager(mock_config, enabled=True)
    m.audio_backend = 'alsa'
    mock_popen = mocker.patch('mpv_manager.subprocess.Popen')
    mocker.patch('builtins.open', mocker.mock_open())

    m._launch_vlc_filler(8081, 'filler')

    args = mock_popen.call_args[0][0]
    assert '--aout' in args
    aout_idx = args.index('--aout')
    assert args[aout_idx + 1] == 'alsa'
    assert '--alsa-audio-device' in args


def test_vlc_filler_launch_pipewire_command(mock_config, mocker):
    """When audio_backend='pipewire', VLC filler uses --aout pulse (PipeWire compat)."""
    mocker.patch('mpv_manager.is_pi', return_value=False)
    m = MpvManager(mock_config, enabled=True)
    m.audio_backend = 'pipewire'
    mock_popen = mocker.patch('mpv_manager.subprocess.Popen')
    mocker.patch('builtins.open', mocker.mock_open())

    m._launch_vlc_filler(8081, 'filler')

    args = mock_popen.call_args[0][0]
    assert '--aout' in args
    aout_idx = args.index('--aout')
    assert args[aout_idx + 1] == 'pulse'
    assert '--alsa-audio-device' not in args
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_mpv_manager.py::test_mpv_launch_pipewire_command tests/unit/test_mpv_manager.py::test_vlc_filler_launch_pipewire_command -v`
Expected: FAIL (mpv still uses `--ao=alsa`, VLC still uses `--aout alsa`)

- [ ] **Step 7: Update _launch_mpv_karaoke to use audio_backend**

In `kj-controller/mpv_manager.py`, replace the command list in `_launch_mpv_karaoke` (lines 360-370):

```python
        if self.audio_backend == 'pipewire':
            log_message("Launching mpv karaoke with PipeWire audio...", self.config)
            command = [
                'mpv', '--idle',
                '--fs',
                '--ao=pipewire',
                '--af=@rb:rubberband',
                f'--input-ipc-server={self.ipc_socket_path}',
                '--really-quiet',
                '--keep-open=no',
                '--no-input-default-bindings',
                '--no-osc',
            ]
        else:
            log_message(f"Launching mpv karaoke with audio device 'alsa/{self.audio_device}'...", self.config)
            command = [
                'mpv', '--idle',
                '--fs',
                '--ao=alsa',
                f'--audio-device=alsa/{self.audio_device}',
                '--af=@rb:rubberband',
                f'--input-ipc-server={self.ipc_socket_path}',
                '--really-quiet',
                '--keep-open=no',
                '--no-input-default-bindings',
                '--no-osc',
            ]
```

- [ ] **Step 8: Update _launch_vlc_filler to use audio_backend**

In `kj-controller/mpv_manager.py`, replace the VLC command list in `_launch_vlc_filler` (lines 407-416):

```python
        if self.audio_backend == 'pipewire':
            log_message(f"Launching VLC filler on port {port} with PipeWire audio...", self.config)
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(port),
                '--http-password', password,
                '--no-video-title-show',
                '--aout', 'pulse',
            ]
        else:
            log_message(f"Launching VLC filler on port {port} with audio device '{self.audio_device}'...", self.config)
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(port),
                '--http-password', password,
                '--no-video-title-show',
                '--aout', 'alsa',
                '--alsa-audio-device', self.audio_device,
            ]
```

- [ ] **Step 9: Run all mpv_manager tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_mpv_manager.py -v`
Expected: ALL PASS

- [ ] **Step 10: Also reset audio_backend in restart_instances**

In `restart_instances()` (line 524), `self.pitch_semitones = 0` is reset. Do NOT reset `audio_backend` here — it's intentionally set by the AudioMonitor before calling restart. No code change needed, just verify the existing `restart_instances` doesn't touch `audio_backend`.

- [ ] **Step 11: Commit**

```bash
git add kj-controller/mpv_manager.py kj-controller/tests/unit/test_mpv_manager.py
git commit -m "feat: add audio_backend property to MpvManager for PipeWire support"
```

---

### Task 2: Create AudioMonitor class

**Files:**
- Create: `kj-controller/audio_monitor.py`
- Create: `kj-controller/tests/unit/test_audio_monitor.py`

The AudioMonitor manages the full lifecycle: PipeWire profile switching, ffmpeg capture process, stream serving, and cleanup.

- [ ] **Step 1: Write failing tests for AudioMonitor**

Create `kj-controller/tests/unit/test_audio_monitor.py`:

```python
"""Tests for AudioMonitor: lifecycle, PipeWire switching, ffmpeg process management."""

import threading
import time

import pytest

from audio_monitor import AudioMonitor


@pytest.fixture
def mock_mpv(mock_config, mocker):
    from mpv_manager import MpvManager
    m = MpvManager(mock_config, enabled=False)
    m.restart_instances = mocker.Mock()
    return m


@pytest.fixture
def monitor(mock_mpv, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    return AudioMonitor(mock_mpv)


# --- Initial state ---

def test_initial_state(monitor):
    assert monitor.active is False
    assert monitor.ffmpeg_proc is None
    assert monitor._client_connected is False


# --- Start ---

def test_start_switches_pipewire_to_hdmi(monitor, mocker):
    mock_run = mocker.patch('audio_monitor.subprocess.run')
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = mocker.Mock()
    mock_popen.return_value.poll = mocker.Mock(return_value=None)

    monitor.start()

    # Check pactl was called to switch to HDMI profile
    calls = [c for c in mock_run.call_args_list if 'pactl' in str(c)]
    assert len(calls) >= 1
    pactl_args = calls[-1][0][0]
    assert 'set-card-profile' in pactl_args
    assert 'output:hdmi-stereo+input:analog-stereo' in pactl_args


def test_start_sets_audio_backend_to_pipewire(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    assert monitor.mpv.audio_backend == 'pipewire'


def test_start_restarts_mpv_instances(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    monitor.mpv.restart_instances.assert_called_once()


def test_start_launches_ffmpeg(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = mocker.Mock()
    mock_popen.return_value.poll = mocker.Mock(return_value=None)

    monitor.start()

    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert args[0] == 'ffmpeg'
    assert 'libmp3lame' in args


def test_start_sets_active(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    assert monitor.active is True


def test_start_idempotent(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    monitor.mpv.restart_instances.reset_mock()
    monitor.start()  # second call should be no-op
    monitor.mpv.restart_instances.assert_not_called()


# --- Stop ---

def test_stop_kills_ffmpeg(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_proc = mocker.Mock()
    mock_proc.stdout = mocker.Mock()
    mock_proc.poll = mocker.Mock(return_value=None)
    mock_popen.return_value = mock_proc

    monitor.start()
    monitor.stop()

    mock_proc.terminate.assert_called_once()


def test_stop_restores_alsa_backend(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    monitor.stop()
    assert monitor.mpv.audio_backend == 'alsa'


def test_stop_restarts_instances(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    monitor.mpv.restart_instances.reset_mock()
    monitor.stop()
    monitor.mpv.restart_instances.assert_called_once()


def test_stop_switches_pipewire_to_analog(monitor, mocker):
    mock_run = mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')

    monitor.start()
    mock_run.reset_mock()
    monitor.stop()

    calls = [c for c in mock_run.call_args_list if 'pactl' in str(c)]
    assert len(calls) >= 1
    pactl_args = calls[-1][0][0]
    assert 'analog-stereo' in str(pactl_args)


def test_stop_sets_inactive(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    monitor.stop()
    assert monitor.active is False


def test_stop_when_not_active_is_noop(monitor, mocker):
    monitor.stop()  # should not raise
    assert monitor.active is False


# --- Status ---

def test_status_when_inactive(monitor):
    assert monitor.status() == {'active': False}


def test_status_when_active(monitor, mocker):
    mocker.patch('audio_monitor.subprocess.Popen')
    monitor.start()
    status = monitor.status()
    assert status['active'] is True
    assert 'stream_url' in status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_audio_monitor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audio_monitor'`

- [ ] **Step 3: Implement AudioMonitor class**

Create `kj-controller/audio_monitor.py`:

```python
"""AudioMonitor: remote audio streaming via PipeWire capture + ffmpeg."""

import subprocess
import threading

from utils import log_message

# PipeWire constants for NomadPC Intel HDA card
PW_CARD = 'alsa_card.pci-0000_00_1f.3'
PW_HDMI_PROFILE = 'output:hdmi-stereo+input:analog-stereo'
PW_ANALOG_PROFILE = 'output:analog-stereo+input:analog-stereo'
PW_MONITOR_SOURCE = 'alsa_output.pci-0000_00_1f.3.hdmi-stereo.monitor'
PACTL_ENV_PREFIX = ['sudo', '-u', 'nomad', 'env', 'XDG_RUNTIME_DIR=/run/user/1000']


class AudioMonitor:
    """Manages remote audio monitoring via PipeWire HDMI capture → ffmpeg MP3 stream."""

    def __init__(self, mpv_manager, config=None):
        self.mpv = mpv_manager
        self.config = config or {}
        self.active = False
        self.ffmpeg_proc = None
        self._client_connected = False
        self._drain_thread = None
        self._lock = threading.Lock()

    def _run_pactl(self, *args):
        """Run a pactl command as the nomad user."""
        cmd = PACTL_ENV_PREFIX + ['pactl'] + list(args)
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log_message(f"AudioMonitor: pactl failed: {e}", self.config)

    def start(self):
        """Enable audio monitor: switch to PipeWire, restart players, start ffmpeg capture."""
        with self._lock:
            if self.active:
                return

            log_message("AudioMonitor: starting — switching to PipeWire HDMI...", self.config)

            # Switch PipeWire to HDMI profile
            self._run_pactl('set-card-profile', PW_CARD, PW_HDMI_PROFILE)

            # Switch mpv/VLC to PipeWire output and restart
            self.mpv.audio_backend = 'pipewire'
            self.mpv.restart_instances()

            # Start ffmpeg capture from PipeWire monitor source
            ffmpeg_cmd = [
                'ffmpeg',
                '-f', 'pulse',
                '-i', PW_MONITOR_SOURCE,
                '-c:a', 'libmp3lame',
                '-b:a', '128k',
                '-f', 'mp3',
                '-fflags', '+nobuffer',
                '-flags', '+low_delay',
                'pipe:1',
            ]
            env = {'XDG_RUNTIME_DIR': '/run/user/1000', 'HOME': '/home/nomad'}
            self.ffmpeg_proc = subprocess.Popen(
                PACTL_ENV_PREFIX[:4] + ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )

            # Start drain thread to prevent pipe backpressure when no client is connected
            self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
            self._drain_thread.start()

            self.active = True
            log_message("AudioMonitor: streaming active.", self.config)

    def stop(self):
        """Disable audio monitor: stop ffmpeg, restore ALSA, restart players."""
        with self._lock:
            if not self.active:
                return

            log_message("AudioMonitor: stopping...", self.config)

            # Kill ffmpeg
            if self.ffmpeg_proc:
                self.ffmpeg_proc.terminate()
                try:
                    self.ffmpeg_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.ffmpeg_proc.kill()
                self.ffmpeg_proc = None

            self._client_connected = False
            self.active = False

            # Restore ALSA audio backend and restart players
            self.mpv.audio_backend = 'alsa'
            self.mpv.restart_instances()

            # Switch PipeWire back to analog profile
            self._run_pactl('set-card-profile', PW_CARD, PW_ANALOG_PROFILE)

            log_message("AudioMonitor: stopped, ALSA restored.", self.config)

    def status(self):
        """Return current monitor status."""
        if not self.active:
            return {'active': False}
        return {
            'active': True,
            'stream_url': '/audio-monitor/stream',
        }

    def stream_generator(self):
        """Yield MP3 chunks from ffmpeg stdout. For use in Flask streaming response."""
        if not self.active or not self.ffmpeg_proc:
            return

        self._client_connected = True
        try:
            while self.active and self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
                chunk = self.ffmpeg_proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            self._client_connected = False

    def _drain_loop(self):
        """Discard ffmpeg output when no client is connected to prevent pipe blocking."""
        while self.active and self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            if not self._client_connected:
                try:
                    self.ffmpeg_proc.stdout.read(4096)
                except (OSError, ValueError):
                    break
            else:
                # Client is reading — sleep briefly to avoid busy-wait
                import time
                time.sleep(0.1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_audio_monitor.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/audio_monitor.py kj-controller/tests/unit/test_audio_monitor.py
git commit -m "feat: add AudioMonitor class for remote audio streaming"
```

---

### Task 3: Add Flask routes and wire into app

**Files:**
- Modify: `kj-controller/routes.py`
- Modify: `kj-controller/app.py`
- Test: `kj-controller/tests/unit/test_audio_monitor.py` (add route tests)

- [ ] **Step 1: Write failing route tests**

Add to `kj-controller/tests/unit/test_audio_monitor.py`:

```python
# --- Route tests ---

@pytest.fixture
def app_with_monitor(mock_config):
    from app import create_app
    app = create_app(config=mock_config)
    app.config['TESTING'] = True

    from audio_monitor import AudioMonitor
    app.audio_monitor = AudioMonitor(app.vlc, mock_config)
    yield app
    app.catalog.close()


@pytest.fixture
def client(app_with_monitor):
    with app_with_monitor.test_client() as c:
        yield c


def test_status_route_inactive(client):
    resp = client.get('/audio-monitor/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['active'] is False


def test_start_route(client, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')
    resp = client.post('/audio-monitor/start', json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert 'stream_url' in data


def test_stop_route(client, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')
    # Start first
    client.post('/audio-monitor/start', json={})
    resp = client.post('/audio-monitor/stop', json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True


def test_stream_route_when_inactive(client):
    resp = client.get('/audio-monitor/stream')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_audio_monitor.py::test_status_route_inactive -v`
Expected: FAIL (404 — route doesn't exist yet)

- [ ] **Step 3: Wire AudioMonitor into app.py (create_app)**

In `kj-controller/app.py`, add import at top (after line 22):

```python
from audio_monitor import AudioMonitor
```

In `create_app()`, after `flask_app.vlc = MpvManager(...)` (after line 69), add:

```python
    flask_app.audio_monitor = AudioMonitor(flask_app.vlc, cfg)
```

In `start_app()`, after `flask_app.vlc = vlc` (after line 174), add:

```python
    flask_app.audio_monitor = AudioMonitor(vlc, cfg)
```

- [ ] **Step 4: Add routes to routes.py**

In `kj-controller/routes.py`, add before the `# --- System Control ---` comment (line 1626):

```python
# --- Audio Monitor Routes ---

@routes_bp.route('/audio-monitor/status', methods=['GET'])
def audio_monitor_status():
    """Returns audio monitor state."""
    return jsonify(current_app.audio_monitor.status())


@routes_bp.route('/audio-monitor/start', methods=['POST'])
def audio_monitor_start():
    """Enable audio monitor: switch to PipeWire, start capture stream."""
    monitor = current_app.audio_monitor
    if monitor.active:
        return jsonify({'success': True, 'stream_url': '/audio-monitor/stream', 'message': 'Already running.'})
    threading.Thread(target=monitor.start).start()
    return jsonify({'success': True, 'stream_url': '/audio-monitor/stream'})


@routes_bp.route('/audio-monitor/stop', methods=['POST'])
def audio_monitor_stop():
    """Disable audio monitor: stop capture, restore ALSA."""
    monitor = current_app.audio_monitor
    if not monitor.active:
        return jsonify({'success': True, 'message': 'Already stopped.'})
    threading.Thread(target=monitor.stop).start()
    return jsonify({'success': True})


@routes_bp.route('/audio-monitor/stream', methods=['GET'])
def audio_monitor_stream():
    """Chunked HTTP audio stream (audio/mpeg). Single client only."""
    from flask import Response
    monitor = current_app.audio_monitor
    if not monitor.active or not monitor.ffmpeg_proc:
        return jsonify({'error': 'Audio monitor not active'}), 404
    if monitor._client_connected:
        return jsonify({'error': 'Another client is already connected'}), 409
    return Response(monitor.stream_generator(), mimetype='audio/mpeg')
```

- [ ] **Step 5: Add audio monitor stop to av_reset**

In `kj-controller/routes.py`, in the `av_reset()` function (around line 1570), add before the `script_path` line:

```python
    # Stop audio monitor if active (must happen before ALSA reset)
    if hasattr(current_app, 'audio_monitor') and current_app.audio_monitor.active:
        log_message("AV reset: stopping audio monitor first...", cfg)
        current_app.audio_monitor.stop()
```

- [ ] **Step 6: Run route tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest tests/unit/test_audio_monitor.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-remote-audio-monitor/kj-controller && pytest -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add kj-controller/routes.py kj-controller/app.py kj-controller/tests/unit/test_audio_monitor.py
git commit -m "feat: add audio monitor routes and wire into app"
```

---

### Task 4: Add UI section to AV Output modal

**Files:**
- Modify: `kj-controller/templates/index.html`
- Modify: `kj-controller/static/app.js`

- [ ] **Step 1: Add Audio Monitor section to HTML**

In `kj-controller/templates/index.html`, after the "Browser Audio Output" section closing `</div>` (after line 654), add:

```html
                <!-- Audio Monitor section -->
                <div class="av-section">
                    <div class="av-section-title">Audio Monitor (Dev)</div>
                    <div id="av-monitor-info"></div>
                    <div class="av-control-row">
                        <p class="av-help">Streams audio over HTTP for remote listening. Switches players to PipeWire (~3s restart).</p>
                        <div id="av-monitor-listen" class="hidden" style="margin:6px 0;">
                            <code id="av-monitor-url" style="font-size:0.8em;color:#aaa;user-select:all;"></code>
                        </div>
                        <button class="system-btn" id="av-monitor-btn" onclick="toggleAudioMonitor()">Start Monitor</button>
                    </div>
                </div>
```

- [ ] **Step 2: Add JavaScript for audio monitor**

In `kj-controller/static/app.js`, after the `avReset()` function (after line 1175), add:

```javascript
// --- Audio Monitor ---

function renderAvMonitorSection(monitorData) {
    const info = document.getElementById('av-monitor-info');
    const btn = document.getElementById('av-monitor-btn');
    const listenDiv = document.getElementById('av-monitor-listen');
    const urlEl = document.getElementById('av-monitor-url');

    if (!monitorData || !monitorData.active) {
        info.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                <span class="av-dot av-dot-off"></span>
                <span class="av-info-value">Off</span>
            </div>`;
        btn.textContent = 'Start Monitor';
        btn.onclick = () => toggleAudioMonitor(true);
        listenDiv.classList.add('hidden');
    } else {
        info.innerHTML = `
            <div class="av-info-row">
                <span class="av-info-label">Status</span>
                <span class="av-dot av-dot-ok"></span>
                <span class="av-info-value">Streaming (PipeWire HDMI)</span>
            </div>`;
        btn.textContent = 'Stop Monitor';
        btn.onclick = () => toggleAudioMonitor(false);
        const host = location.hostname || 'nomadpc.local';
        const port = location.port || (location.protocol === 'https:' ? '443' : '80');
        const proto = location.protocol;
        urlEl.textContent = `ffplay ${proto}//${host}:${port}/audio-monitor/stream`;
        listenDiv.classList.remove('hidden');
    }
}

async function toggleAudioMonitor(start) {
    const btn = document.getElementById('av-monitor-btn');
    btn.disabled = true;
    btn.textContent = start ? 'Starting…' : 'Stopping…';
    const endpoint = start ? '/audio-monitor/start' : '/audio-monitor/stop';
    log(start ? 'Starting audio monitor (switching to PipeWire)…' : 'Stopping audio monitor (restoring ALSA)…');
    const data = await apiCall(endpoint, {});
    if (data && data.success) {
        log(start ? 'Audio monitor started.' : 'Audio monitor stopped.', 'success');
        // Wait for restart to complete before refreshing
        setTimeout(avRefresh, start ? 5000 : 4000);
    } else {
        btn.disabled = false;
        btn.textContent = start ? 'Start Monitor' : 'Stop Monitor';
    }
}
```

- [ ] **Step 3: Wire monitor data into the AV modal render**

In `kj-controller/static/app.js`, update the `renderAvModal()` function (around line 885) to include the monitor section. Add after `populateAvBrowserAudioSelect(data.audio);`:

```javascript
    renderAvMonitorSection(data.audio_monitor);
```

- [ ] **Step 4: Add audio_monitor to /av/status response**

In `kj-controller/routes.py`, in the `av_status()` function, add before the `return jsonify(...)` line (line 1536):

```python
    audio_monitor = {}
    if hasattr(current_app, 'audio_monitor'):
        audio_monitor = current_app.audio_monitor.status()

    return jsonify({'video': video, 'audio': audio, 'health': health, 'audio_monitor': audio_monitor})
```

(Replace the existing `return jsonify(...)` on line 1536.)

- [ ] **Step 5: Verify UI renders in browser**

Open the KJ Controller web UI, click AV Output, and verify the "Audio Monitor (Dev)" section appears with an "Off" status and "Start Monitor" button.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/app.js kj-controller/routes.py
git commit -m "feat: add audio monitor UI section to AV Output modal"
```

---

### Task 5: Update docs and integration test

**Files:**
- Modify: `docs/AUDIO.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Add Audio Monitor section to AUDIO.md**

Add a new section to `docs/AUDIO.md` after the "NomadPC" section (after the PipeWire Coexistence subsection, before `## NomadPi`):

```markdown
### Remote Audio Monitor

The KJ Controller includes a remote audio monitor for dev/testing. When enabled via the AV Output modal, it:

1. Switches PipeWire to the HDMI profile (`output:hdmi-stereo+input:analog-stereo`)
2. Restarts mpv with `--ao=pipewire` and VLC filler with `--aout pulse`
3. Runs ffmpeg to capture from PipeWire's HDMI monitor source and encode to MP3
4. Serves the stream at `GET /audio-monitor/stream`

**Listen from another machine:**
```bash
ffplay http://nomadpc.local/audio-monitor/stream
```

**Important notes:**
- Enabling/disabling restarts mpv and VLC (~3 second interruption)
- Single client at a time
- State is NOT persisted — after service restart, monitor is off and audio returns to ALSA
- "Reset All" in the AV Output modal stops the monitor and restores ALSA mode
- PipeWire HDMI output was tested and confirmed working on NomadPC (2026-04-15)
```

- [ ] **Step 2: Add changelog entry**

Add to the top of `docs/CHANGELOG.md`:

```markdown
## 2026-04-15
- **Audio Monitor:** Added remote audio monitoring via AV Output modal. Streams live audio over HTTP for dev/testing. Uses PipeWire HDMI capture + ffmpeg MP3 encoding.
```

- [ ] **Step 3: Commit**

```bash
git add docs/AUDIO.md docs/CHANGELOG.md
git commit -m "docs: add remote audio monitor documentation"
```

---

### Task 6: End-to-end verification on NomadPC

This task requires SSH access to NomadPC and cannot be automated in tests.

- [ ] **Step 1: Deploy to NomadPC**

Push the branch and trigger deploy, or manually copy files via SSH.

- [ ] **Step 2: Verify AV Output modal shows Audio Monitor section**

Open `http://nomadpc.local` → AV Output → verify "Audio Monitor (Dev)" section visible with "Off" status.

- [ ] **Step 3: Start the audio monitor**

Click "Start Monitor". Verify:
- Button shows "Starting…" then "Streaming (PipeWire HDMI)"
- Players restart (~3s)
- ffplay command is displayed

- [ ] **Step 4: Listen from Mac**

Run the displayed ffplay command from the Mac terminal. Verify audio is heard.

- [ ] **Step 5: Play a karaoke song and verify monitoring works**

Play a song through the KJ Controller. Verify the audio is captured and streamed to the Mac.

- [ ] **Step 6: Stop the monitor**

Click "Stop Monitor". Verify:
- Players restart back to ALSA
- Status shows "Off"
- Filler music resumes through HDMI

- [ ] **Step 7: Test Reset All**

Start the monitor again, then click "Reset All to Known-Good State". Verify the monitor stops and audio returns to ALSA mode.
