from oracle_measure import parse_mean_volume


def test_parse_mean_volume_reads_mean_not_max():
    stderr = (
        "[Parsed_volumedetect_0 @ 0x0] n_samples: 1000\n"
        "[Parsed_volumedetect_0 @ 0x0] mean_volume: -41.5 dB\n"
        "[Parsed_volumedetect_0 @ 0x0] max_volume: -6.4 dB\n"
    )
    assert parse_mean_volume(stderr) == -41.5


def test_parse_mean_volume_missing_returns_none():
    assert parse_mean_volume("no volume info here") is None
