"""Tests for filler_library: naming, labels, clip parsing, ffmpeg command, conversion, jobs."""

import os
import shutil
import subprocess
import time

import pytest

import filler_library as fl


@pytest.fixture
def cfg(tmp_path):
    return {'filler_music_dir': str(tmp_path)}


# --- names / paths ---

def test_slugify_strips_unsafe_chars_and_dots():
    assert fl.slugify_filename('Spooky: Mix?') == 'Spooky_ Mix_'
    assert '/' not in fl.slugify_filename('a/b\\c')
    assert not fl.slugify_filename('...').startswith('.')
    assert fl.slugify_filename('   ') == 'filler'


def test_unique_name_adds_suffix(tmp_path):
    (tmp_path / 'a.mp3').write_bytes(b'x')
    (tmp_path / 'a-2.mp3').write_bytes(b'x')
    assert fl.unique_name(str(tmp_path), 'a') == 'a-3.mp3'
    assert fl.unique_name(str(tmp_path), 'b') == 'b.mp3'


@pytest.mark.parametrize('name', ['../x.mp3', 'sub/x.mp3', '.labels.mp3', 'x.txt', '', None])
def test_track_path_rejects_unsafe(tmp_path, name):
    (tmp_path / 'x.mp3').write_bytes(b'x')
    assert fl.track_path(str(tmp_path), name) is None


def test_track_path_accepts_existing_audio(tmp_path):
    (tmp_path / 'x.mp3').write_bytes(b'x')
    assert fl.track_path(str(tmp_path), 'x.mp3') == str(tmp_path / 'x.mp3')


# --- labels ---

def test_labels_roundtrip_and_clear(tmp_path, cfg):
    d = str(tmp_path)
    (tmp_path / 'x.mp3').write_bytes(b'x')
    fl.set_label(d, 'x.mp3', ' Pretty Name ')
    assert fl.list_tracks(cfg)[0]['label'] == 'Pretty Name'
    fl.set_label(d, 'x.mp3', '')
    assert fl.list_tracks(cfg)[0]['label'] == 'x.mp3'


def test_list_tracks_ignores_hidden_and_non_audio(tmp_path, cfg):
    for n in ['a.mp3', '.hidden.mp3', 'notes.txt', '.filler-labels.json']:
        (tmp_path / n).write_bytes(b'x')
    assert [t['name'] for t in fl.list_tracks(cfg, current='a.mp3')] == ['a.mp3']
    assert fl.list_tracks(cfg, current='a.mp3')[0]['current'] is True


def test_delete_blocks_current_and_removes_label(tmp_path, cfg):
    (tmp_path / 'a.mp3').write_bytes(b'x')
    (tmp_path / 'b.mp3').write_bytes(b'x')
    fl.set_label(str(tmp_path), 'b.mp3', 'Bee')
    with pytest.raises(fl.FillerError, match='playing now'):
        fl.delete_track(cfg, 'a.mp3', current='a.mp3')
    fl.delete_track(cfg, 'b.mp3', current='a.mp3')
    assert not (tmp_path / 'b.mp3').exists()
    assert 'b.mp3' not in fl.load_labels(str(tmp_path))


# --- clip parsing ---

@pytest.mark.parametrize('text,expected', [
    ('90', 90), ('1:30', 90), ('1:02:03', 3723), ('0:05.5', 5.5), ('', None), (None, None)])
def test_parse_timestamp(text, expected):
    assert fl.parse_timestamp(text) == expected


@pytest.mark.parametrize('bad', ['abc', '1:2:3:4', '-5', '1:xx'])
def test_parse_timestamp_rejects(bad):
    with pytest.raises(fl.FillerError):
        fl.parse_timestamp(bad)


def test_parse_clip_defaults_and_limits():
    assert fl.parse_clip('', '') == (0.0, fl.DEFAULT_CLIP_S)
    assert fl.parse_clip('', '', default_duration=None) == (0.0, None)
    assert fl.parse_clip('0:10', '1:00') == (10.0, 60.0)
    with pytest.raises(fl.FillerError):
        fl.parse_clip('', '0')
    with pytest.raises(fl.FillerError):
        fl.parse_clip('', str(fl.MAX_CLIP_S + 1))


# --- ffmpeg command ---

def test_build_ffmpeg_cmd_options():
    cmd = fl.build_ffmpeg_cmd('in.webm', 'out.mp3', start=30, duration=20, normalize=True)
    assert cmd[cmd.index('-ss') + 1] == '30.000'
    assert cmd.index('-ss') < cmd.index('-i')  # fast seek before input
    assert cmd[cmd.index('-t') + 1] == '20.000'
    assert '-vn' in cmd and 'libmp3lame' in cmd and 'loudnorm' in cmd[cmd.index('-af') + 1]
    plain = fl.build_ffmpeg_cmd('in.wav', 'out.mp3')
    assert '-ss' not in plain and '-t' not in plain and '-af' not in plain


def test_convert_reports_ffmpeg_failure(tmp_path):
    src = tmp_path / 'junk.bin'
    src.write_bytes(b'not audio')
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not installed')
    with pytest.raises(fl.FillerError, match="Couldn't convert"):
        fl.convert_to_mp3(str(src), str(tmp_path / 'o.mp3'))


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')
def test_file_import_clips_and_installs(tmp_path, cfg):
    src = tmp_path / 'tone.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
                    str(src)], check=True)
    job_id = fl.start_file_import(cfg, str(src), 'tone.wav', 'My Tone', '1', '3', False)
    for _ in range(100):
        job = fl.jobs.get(job_id)
        if job['state'] != 'running':
            break
        time.sleep(0.1)
    assert job['state'] == 'done', job
    track = fl.list_tracks(cfg)[0]
    assert track['label'] == 'My Tone' and track['name'] == 'My Tone.mp3'
    assert 2.5 < track['duration'] < 3.6
    assert not src.exists()  # upload temp removed
    assert not [n for n in os.listdir(tmp_path) if n.startswith('.import-')]


# --- jobs ---

def test_second_job_rejected_while_running():
    mgr = fl.JobManager()
    import threading
    gate = threading.Event()
    mgr.start('x', lambda update: gate.wait(5) and 'done.mp3')
    with pytest.raises(fl.FillerError, match='still running'):
        mgr.start('y', lambda update: 'n.mp3')
    gate.set()


def test_job_error_is_captured():
    mgr = fl.JobManager()

    def boom(update):
        raise fl.FillerError('nope')

    jid = mgr.start('x', boom)
    for _ in range(50):
        job = mgr.get(jid)
        if job['state'] != 'running':
            break
        time.sleep(0.05)
    assert job['state'] == 'error' and job['error'] == 'nope'


# --- YouTube ---

@pytest.mark.parametrize('url', ['https://example.com/watch?v=1', 'file:///etc/passwd', 'notaurl', ''])
def test_validate_youtube_url_rejects_non_youtube(url):
    with pytest.raises(fl.FillerError):
        fl.validate_youtube_url(url)


def test_validate_youtube_url_accepts():
    assert fl.validate_youtube_url('https://youtu.be/abc') == 'https://youtu.be/abc'
    assert fl.validate_youtube_url('https://www.youtube.com/watch?v=abc')


def test_truncated_download_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, 'probe_duration', lambda p: 6.0)
    with pytest.raises(fl.FillerError, match='came out short'):
        fl._check_not_truncated('x', 4000, 30, 20)
    monkeypatch.setattr(fl, 'probe_duration', lambda p: 19.8)
    fl._check_not_truncated('x', 4000, 30, 20)  # within tolerance


def test_cleanup_stale_imports(tmp_path, cfg):
    (tmp_path / '.import-abc').mkdir()
    (tmp_path / '.upload-xyz').write_bytes(b'x')
    (tmp_path / 'keep.mp3').write_bytes(b'x')
    fl.cleanup_stale_imports(cfg)
    assert sorted(os.listdir(tmp_path)) == ['keep.mp3']


# --- loop smoothing ---

def test_build_loop_cmd_crossfades_long_tracks():
    cmd = fl.build_loop_cmd('in.wav', 'out.mp3', 60)
    graph = cmd[cmd.index('-filter_complex') + 1]
    assert 'acrossfade=d=3.0' in graph and 'atrim=3.0:57.000' in graph


def test_build_loop_cmd_short_tracks_get_plain_fades():
    cmd = fl.build_loop_cmd('in.wav', 'out.mp3', 6)
    assert '-filter_complex' not in cmd
    assert 'afade=t=in' in cmd[cmd.index('-af') + 1] and 'afade=t=out' in cmd[cmd.index('-af') + 1]


def _samples(path):
    import array
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', '8000',
                          '-f', 's16le', '-'], capture_output=True, check=True).stdout
    a = array.array('h')
    a.frombytes(raw)
    return a


def _rms(xs):
    return (sum(x * x for x in xs) / len(xs)) ** 0.5


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')
def test_smoothed_loop_has_no_loudness_jump_at_the_wrap(tmp_path):
    # Loud first half, quiet second half: a naive repeat jumps quiet -> loud at the wrap.
    src = tmp_path / 'loud-then-quiet.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=24',
                    '-af', "volume='if(lt(t,12),1,0.1)':eval=frame", str(src)], check=True)
    out = tmp_path / 'loop.mp3'
    fl.make_loop(str(src), str(out))
    assert 20.0 < fl.probe_duration(str(out)) < 21.5  # 24s minus the 3s crossfade

    def jump(path):
        x = _samples(path)
        end, start = _rms(x[-400:]), _rms(x[:400])  # 50 ms either side of the wrap at 8 kHz
        return max(end, start) / min(end, start)

    assert jump(str(src)) > 5      # the original hard-stops
    assert jump(str(out)) < 2      # the smoothed copy carries on
