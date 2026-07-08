import numpy as np
from align_core import (first_vocal_onset, variant_offsets, clip_cut, emit_af,
                        OffsetRow, parse_decision, apply_decision, read_offsets, write_offsets)

def test_first_vocal_onset_after_silence():
    sr=8000; sig=np.concatenate([np.zeros(sr*2), 0.5*np.sin(2*np.pi*220*np.arange(sr*3)/sr)]).astype('f4')
    assert abs(first_vocal_onset(sig, sr) - 2.0) < 0.1

def test_first_vocal_onset_immediate():
    sr=8000; sig=(0.5*np.sin(2*np.pi*220*np.arange(sr*2)/sr)).astype('f4')
    assert first_vocal_onset(sig, sr) < 0.2

def test_variant_offsets_default():
    assert variant_offsets(5.0) == [5.0, 4.9, 5.1, 4.8, 5.2]

def test_variant_offsets_clamps_negative():
    assert all(o >= 0 for o in variant_offsets(0.05))

def test_clip_cut_correct_candidate_matches_measured():
    vstart, gstart, dur = clip_cut(measured_s=5.0, onset_s=10.0, candidate_s=5.0)
    assert abs(vstart - 12.0) < 1e-6      # 5 + 10 - 3
    assert abs(gstart - 7.0) < 1e-6       # onset - before  (candidate==measured)
    assert abs(dur - 15.0) < 1e-6

def test_clip_cut_wrong_candidate_shifts_guide():
    # candidate 0.1s later than measured -> guide cut 0.1s earlier so it plays late in-clip
    _, gstart, _ = clip_cut(measured_s=5.0, onset_s=10.0, candidate_s=5.1)
    assert abs(gstart - 6.9) < 1e-6

def test_emit_af_shape():
    af = emit_af(4.98, 200.0)
    assert "adelay=4980:all=1" in af and "atrim=0:200.000" in af

def test_parse_decision():
    assert parse_decision("confirm") == ("confirm", None)
    assert parse_decision("exclude") == ("exclude", None)
    assert parse_decision("needs-finer") == ("needs-finer", None)
    assert parse_decision("offset_ms=4870") == ("offset", 4.870)

def test_apply_decision_offset_sets_human_source():
    r = OffsetRow("NOMAD-0300",5.0,0.9,"confirmed",200,190,10.0,"measured","active")
    r2 = apply_decision(r, "offset", 4.87)
    assert r2.offset_s == 4.87 and r2.source == "human" and r2.status == "active"

def test_apply_decision_exclude():
    r = OffsetRow("NOMAD-0300",5.0,0.1,"needs-review",200,190,10.0,"measured","active")
    assert apply_decision(r, "exclude", None).status == "excluded"

def test_offsets_roundtrip(tmp_path):
    rows={"NOMAD-0300":OffsetRow("NOMAD-0300",5.0,0.9,"confirmed",200,190,10.0,"measured","active")}
    p=tmp_path/"o.csv"; write_offsets(str(p), rows)
    assert read_offsets(str(p))["NOMAD-0300"].offset_s == 5.0
