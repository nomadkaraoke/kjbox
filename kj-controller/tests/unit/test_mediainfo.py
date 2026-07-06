"""Unit tests for mediainfo.parse_media_info — the pure ffprobe-JSON normalizer.

The parser is separated from the subprocess call so it can be exercised against
canned ffprobe payloads without touching the filesystem or ffprobe.
"""
import mediainfo


# A representative `ffprobe -show_format -show_streams -print_format json` payload
# for a 720p H.264/AAC MP4 (the common karaoke video case).
MP4_PAYLOAD = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "profile": "High",
            "width": 1280,
            "height": 720,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30000/1001",
            "bit_rate": "4000000",
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "128000",
        },
    ],
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "218.500000",
        "size": "12345678",
        "bit_rate": "4200000",
    },
}


def test_parses_video_stream():
    info = mediainfo.parse_media_info(MP4_PAYLOAD)
    assert info["ok"] is True
    assert info["video"]["codec"] == "h264"
    assert info["video"]["width"] == 1280
    assert info["video"]["height"] == 720
    assert info["video"]["pix_fmt"] == "yuv420p"
    # 30000/1001 ≈ 29.97
    assert round(info["video"]["fps"], 2) == 29.97


def test_parses_audio_stream():
    info = mediainfo.parse_media_info(MP4_PAYLOAD)
    assert info["audio"]["codec"] == "aac"
    assert info["audio"]["sample_rate"] == 48000
    assert info["audio"]["channels"] == 2
    assert info["audio"]["channel_layout"] == "stereo"
    assert info["audio"]["bit_rate"] == 128000


def test_parses_format_container():
    info = mediainfo.parse_media_info(MP4_PAYLOAD)
    assert info["container"] == "mov,mp4,m4a,3gp,3g2,mj2"
    assert info["duration"] == 218.5
    assert info["size_bytes"] == 12345678
    assert info["bit_rate"] == 4200000


def test_size_override_wins_over_format_size():
    # When we know the on-disk size (os.stat), it should override ffprobe's.
    info = mediainfo.parse_media_info(MP4_PAYLOAD, size_bytes=999)
    assert info["size_bytes"] == 999


def test_audio_only_has_no_video():
    payload = {
        "streams": [
            {"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2}
        ],
        "format": {"format_name": "mp3", "duration": "180.0"},
    }
    info = mediainfo.parse_media_info(payload)
    assert info["ok"] is True
    assert info["video"] is None
    assert info["audio"]["codec"] == "mp3"


def test_first_stream_of_each_type_wins():
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480},
            {"codec_type": "video", "codec_name": "mjpeg", "width": 320, "height": 240},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            {"codec_type": "audio", "codec_name": "mp3", "channels": 1},
        ],
        "format": {"format_name": "matroska"},
    }
    info = mediainfo.parse_media_info(payload)
    assert info["video"]["codec"] == "h264"
    assert info["audio"]["codec"] == "aac"


def test_empty_payload_is_not_ok():
    info = mediainfo.parse_media_info({})
    assert info["ok"] is False
    assert info["video"] is None
    assert info["audio"] is None


def test_bad_numeric_fields_do_not_crash():
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": "?", "height": None,
             "avg_frame_rate": "0/0", "bit_rate": "N/A"},
        ],
        "format": {"format_name": "x", "duration": "N/A", "size": "", "bit_rate": None},
    }
    info = mediainfo.parse_media_info(payload)
    assert info["ok"] is True
    assert info["video"]["codec"] == "h264"
    assert info["video"]["fps"] is None
    assert info["video"]["width"] is None
    assert info["duration"] is None
    assert info["bit_rate"] is None
