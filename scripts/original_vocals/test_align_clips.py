from align_core import OffsetRow
from align_clips import select_review, clip_name, ffmpeg_clip_cmd

def _rows(n, verdict):
    return {f"NOMAD-{i:04d}": OffsetRow(f"NOMAD-{i:04d}",5.0,0.9 if verdict=="confirmed" else 0.1,
            verdict,200,190,10.0,"measured","active") for i in range(n)}

def test_select_review_all_flagged_plus_spotcheck():
    rows = {**_rows(100,"confirmed")}
    rows.update({f"NOMAD-9{i:03d}": OffsetRow(f"NOMAD-9{i:03d}",5.0,0.1,"needs-review",200,190,10.0,"measured","active") for i in range(10)})
    flagged, spot = select_review(rows, spot_frac=0.07, seed=1)
    assert len(flagged)==10
    assert 5 <= len(spot) <= 9          # ~7% of 100
    assert set(spot).isdisjoint(flagged)

def test_select_review_deterministic():
    rows=_rows(100,"confirmed")
    assert select_review(rows,seed=1)[1] == select_review(rows,seed=1)[1]

def test_clip_name_encodes_offset():
    assert clip_name("NOMAD-0300","Frightened Rabbit - Square 9",4.98).endswith("__off=4.980s.mp4")

def test_ffmpeg_clip_cmd_from_start_delays_guide_and_amix():
    # first `dur`s from t=0, guide mixed in delayed by the offset (mirrors emit)
    cmd = ffmpeg_clip_cmd("v.mp4","g.flac",4.98,60.0,"out.mp4")
    s=" ".join(cmd)
    assert "adelay=4980:all=1" in s      # guide delayed by the candidate offset
    assert "amix=inputs=2" in s
    assert "-t 60.000" in s              # first 60s
    assert "-ss" not in s                # no seeking — always from the very start
    assert s.endswith("out.mp4")
