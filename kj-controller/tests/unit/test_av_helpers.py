"""Unit tests for AV helper functions in routes.py.

These test the module-level helper functions directly, without a Flask
app context, by importing them from routes and mocking their dependencies.
"""

import subprocess
from unittest.mock import MagicMock, mock_open

import pytest


# ---------------------------------------------------------------------------
# _get_edid_monitor_name
# ---------------------------------------------------------------------------

def _build_edid(name, descriptor_index=0):
    """Build a minimal valid 128-byte EDID with a monitor name descriptor."""
    edid = bytearray(128)
    edid[0:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'
    offset = 54 + descriptor_index * 18
    edid[offset] = 0x00      # not a timing block
    edid[offset + 1] = 0x00
    edid[offset + 2] = 0x00
    edid[offset + 3] = 0xFC  # monitor name tag
    edid[offset + 4] = 0x00
    name_bytes = (name + '\n').ljust(13).encode('ascii')[:13]
    edid[offset + 5: offset + 5 + len(name_bytes)] = name_bytes
    return bytes(edid)


class TestGetEdidMonitorName:
    """Tests for _get_edid_monitor_name()."""

    def test_returns_monitor_name_from_valid_edid(self, mocker):
        """Returns the monitor name when a valid EDID with 0xFC descriptor exists."""
        edid_data = _build_edid("HDMI Splitter")
        mocker.patch('routes.glob.glob', return_value=['/sys/class/drm/card0-HDMI-A-1/edid'])
        mocker.patch('builtins.open', mock_open(read_data=edid_data))

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') == 'HDMI Splitter'

    def test_returns_none_when_no_edid_path(self, mocker):
        """Returns None when no sysfs EDID path exists for the connector."""
        mocker.patch('routes.glob.glob', return_value=[])

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') is None

    def test_returns_none_for_too_short_edid(self, mocker):
        """Returns None when EDID data is shorter than 128 bytes."""
        mocker.patch('routes.glob.glob', return_value=['/sys/class/drm/card0-HDMI-A-1/edid'])
        mocker.patch('builtins.open', mock_open(read_data=b'\x00' * 64))

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') is None

    def test_returns_none_for_invalid_edid_header(self, mocker):
        """Returns None when the EDID header magic bytes don't match."""
        mocker.patch('routes.glob.glob', return_value=['/sys/class/drm/card0-HDMI-A-1/edid'])
        mocker.patch('builtins.open', mock_open(read_data=b'\x00' * 128))

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') is None

    def test_maps_hdmi_to_hdmi_a_connector_name(self, mocker):
        """HDMI-N connector names are mapped to HDMI-A-N in the DRM sysfs glob."""
        captured = []

        def mock_glob(pattern):
            captured.append(pattern)
            return []

        mocker.patch('routes.glob.glob', side_effect=mock_glob)

        from routes import _get_edid_monitor_name
        _get_edid_monitor_name('HDMI-2')
        assert len(captured) == 1
        assert 'HDMI-A-2' in captured[0]

    def test_returns_monitor_name_from_second_descriptor(self, mocker):
        """Finds monitor name in the second descriptor block (descriptor_index=1)."""
        edid_data = _build_edid("SecondDesc", descriptor_index=1)
        mocker.patch('routes.glob.glob', return_value=['/sys/class/drm/card0-HDMI-A-1/edid'])
        mocker.patch('builtins.open', mock_open(read_data=edid_data))

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') == 'SecondDesc'

    def test_returns_none_on_file_open_exception(self, mocker):
        """Returns None gracefully when opening the EDID file raises an exception."""
        mocker.patch('routes.glob.glob', return_value=['/sys/class/drm/card0-HDMI-A-1/edid'])
        mocker.patch('builtins.open', side_effect=PermissionError('denied'))

        from routes import _get_edid_monitor_name
        assert _get_edid_monitor_name('HDMI-1') is None


# ---------------------------------------------------------------------------
# _get_eld_info
# ---------------------------------------------------------------------------

class TestGetEldInfo:
    """Tests for _get_eld_info()."""

    def test_returns_entries_with_monitor_present(self, mocker):
        """Includes ELD entries where monitor_present=1."""
        eld_content = "monitor_present\t1\nmonitor_name\tHDMI Monitor\neld_valid\t1\n"
        mocker.patch('routes.glob.glob', return_value=['/proc/asound/card0/eld#0.0'])
        mocker.patch('builtins.open', mock_open(read_data=eld_content))

        from routes import _get_eld_info
        results = _get_eld_info()
        assert len(results) == 1
        assert results[0]['monitor_name'] == 'HDMI Monitor'

    def test_excludes_entries_without_monitor_present(self, mocker):
        """Ignores ELD entries where monitor_present != 1."""
        eld_content = "monitor_present\t0\nmonitor_name\tSome Monitor\n"
        mocker.patch('routes.glob.glob', return_value=['/proc/asound/card0/eld#0.0'])
        mocker.patch('builtins.open', mock_open(read_data=eld_content))

        from routes import _get_eld_info
        assert _get_eld_info() == []

    def test_returns_empty_when_no_eld_files(self, mocker):
        """Returns empty list when glob finds no ELD files."""
        mocker.patch('routes.glob.glob', return_value=[])

        from routes import _get_eld_info
        assert _get_eld_info() == []

    def test_handles_unreadable_eld_file(self, mocker):
        """Skips ELD files that cannot be opened."""
        mocker.patch('routes.glob.glob', return_value=['/proc/asound/card0/eld#0.0'])
        mocker.patch('builtins.open', side_effect=PermissionError('denied'))

        from routes import _get_eld_info
        assert _get_eld_info() == []


# ---------------------------------------------------------------------------
# _get_pipewire_profile
# ---------------------------------------------------------------------------

PACTL_CARDS_OUTPUT = (
    'Card #44\n'
    '\tName: alsa_card.pci-0000_00_1f.3\n'
    '\tDriver: module-alsa-card\n'
    '\tActive Profile: output:analog-stereo+input:analog-stereo\n'
    '\n'
    'Card #45\n'
    '\tName: alsa_card.usb-some_usb_device\n'
    '\tActive Profile: output:stereo\n'
)


class TestGetPipewireProfile:
    """Tests for _get_pipewire_profile()."""

    def test_returns_active_profile_for_intel_pch_card(self, mocker):
        """Returns the active PipeWire profile for the Intel PCH card."""
        mocker.patch(
            'routes.subprocess.run',
            return_value=MagicMock(stdout=PACTL_CARDS_OUTPUT, returncode=0),
        )

        from routes import _get_pipewire_profile
        result = _get_pipewire_profile()
        assert result == 'output:analog-stereo+input:analog-stereo'

    def test_returns_none_on_subprocess_exception(self, mocker):
        """Returns None when pactl raises an exception."""
        mocker.patch('routes.subprocess.run', side_effect=FileNotFoundError)

        from routes import _get_pipewire_profile
        assert _get_pipewire_profile() is None

    def test_returns_none_when_no_matching_card(self, mocker):
        """Returns None when no Intel PCH card is found in pactl output."""
        mocker.patch(
            'routes.subprocess.run',
            return_value=MagicMock(stdout='Card #1\n\tName: some_other_card\n\tActive Profile: foo\n', returncode=0),
        )

        from routes import _get_pipewire_profile
        assert _get_pipewire_profile() is None

    def test_returns_none_on_timeout(self, mocker):
        """Returns None when pactl times out."""
        mocker.patch('routes.subprocess.run', side_effect=subprocess.TimeoutExpired('pactl', 5))

        from routes import _get_pipewire_profile
        assert _get_pipewire_profile() is None


# ---------------------------------------------------------------------------
# _get_av_video_status
# ---------------------------------------------------------------------------

class TestGetAvVideoStatus:
    """Tests for _get_av_video_status()."""

    def test_returns_empty_on_xrandr_not_found(self, mocker):
        """Returns empty connectors dict when xrandr is not installed."""
        mocker.patch('routes.subprocess.run', side_effect=FileNotFoundError)
        mocker.patch('routes.glob.glob', return_value=[])

        from routes import _get_av_video_status
        result = _get_av_video_status()
        assert result == {'connectors': {}, 'active_output': None}

    def test_returns_empty_on_xrandr_timeout(self, mocker):
        """Returns empty connectors dict when xrandr times out."""
        mocker.patch('routes.subprocess.run', side_effect=subprocess.TimeoutExpired('xrandr', 5))
        mocker.patch('routes.glob.glob', return_value=[])

        from routes import _get_av_video_status
        result = _get_av_video_status()
        assert result == {'connectors': {}, 'active_output': None}


# ---------------------------------------------------------------------------
# _get_av_audio_status
# ---------------------------------------------------------------------------

class TestGetAvAudioStatus:
    """Tests for _get_av_audio_status()."""

    def test_returns_fallback_pcms_on_aplay_timeout(self, mocker):
        """Falls back to default Intel HDA HDMI PCMs when aplay times out."""
        mocker.patch('routes.subprocess.run', side_effect=subprocess.TimeoutExpired('aplay', 5))
        mocker.patch('routes.glob.glob', return_value=[])
        mocker.patch('builtins.open', side_effect=FileNotFoundError)

        from routes import _get_av_audio_status
        result = _get_av_audio_status('hdmiout')
        assert 'hw:0,3' in result['hdmi_pcms']
        assert 'hw:0,7' in result['hdmi_pcms']

    def test_amixer_timeout_defaults_jack_connected_to_false(self, mocker):
        """When amixer times out, jack connected state defaults to False."""
        def mock_run(cmd, **kwargs):
            if 'aplay' in cmd:
                return MagicMock(stdout='card 0: PCH [HDA Intel PCH], device 3: HDMI 0 [HDMI 0]\n')
            raise subprocess.TimeoutExpired('amixer', 5)

        mocker.patch('routes.subprocess.run', side_effect=mock_run)
        mocker.patch('routes.glob.glob', return_value=[])
        mocker.patch('builtins.open', side_effect=FileNotFoundError)

        from routes import _get_av_audio_status
        result = _get_av_audio_status('hdmiout')
        assert 'hw:0,3' in result['hdmi_pcms']
        assert result['hdmi_pcms']['hw:0,3']['connected'] is False

    def test_reads_asound_conf_hw_device_when_present(self, mocker):
        """Parses /etc/asound.conf to determine the active hw:X,Y device alias."""
        asound_content = '# kj-controller config\npcm.hdmiout { type plug slave { pcm "hw:0,7" } }\n'

        def mock_run(cmd, **kwargs):
            return MagicMock(stdout='', returncode=0)

        mocker.patch('routes.subprocess.run', side_effect=mock_run)
        mocker.patch('routes.glob.glob', return_value=[])
        mocker.patch('builtins.open', mock_open(read_data=asound_content))

        from routes import _get_av_audio_status
        result = _get_av_audio_status('hdmiout')
        assert result['asound_hw'] == 'hw:0,7'

    def test_asound_hw_is_none_when_conf_missing(self, mocker):
        """asound_hw is None when /etc/asound.conf does not exist."""
        mocker.patch(
            'routes.subprocess.run',
            return_value=MagicMock(stdout='', returncode=0),
        )
        mocker.patch('routes.glob.glob', return_value=[])
        mocker.patch('builtins.open', side_effect=FileNotFoundError)

        from routes import _get_av_audio_status
        result = _get_av_audio_status('hdmiout')
        assert result['asound_hw'] is None
