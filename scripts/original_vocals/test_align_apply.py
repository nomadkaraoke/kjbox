from align_core import OffsetRow
from align_apply import merge_decisions, emit_cmd

def _row(b,verdict="needs-review"): return OffsetRow(b,5.0,0.1,verdict,200,190,10.0,"measured","active")

def test_merge_offset_and_exclude_and_finer():
    rows={"NOMAD-1":_row("NOMAD-1"),"NOMAD-2":_row("NOMAD-2"),"NOMAD-3":_row("NOMAD-3")}
    decs=[{"brand":"NOMAD-1","decision":"offset_ms=4870"},
          {"brand":"NOMAD-2","decision":"exclude"},
          {"brand":"NOMAD-3","decision":"needs-finer"}]
    updated, finer = merge_decisions(rows, decs)
    assert updated["NOMAD-1"].offset_s==4.87 and updated["NOMAD-1"].source=="human"
    assert updated["NOMAD-2"].status=="excluded"
    assert finer==["NOMAD-3"]

def test_emit_cmd_pads_and_trims_to_video():
    cmd=emit_cmd("g.flac","out.flac",4.98,200.0)
    s=" ".join(cmd)
    assert "adelay=4980:all=1" in s and "atrim=0:200.000" in s and s.endswith("out.flac")
