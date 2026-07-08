from types import SimpleNamespace
from align_measure import build_offset_row
def test_build_offset_row_maps_fields():
    sr = SimpleNamespace(brand_code="NOMAD-0300", offset_s=4.98, peak=0.91,
                         verdict="confirmed", video_dur=200.0, audio_dur=190.0, onset_s=-1.0)
    row = build_offset_row(sr, onset_s=10.2)
    assert row.brand=="NOMAD-0300" and row.offset_s==4.98 and row.verdict=="confirmed"
    assert row.onset_s==10.2 and row.source=="measured" and row.status=="active"
