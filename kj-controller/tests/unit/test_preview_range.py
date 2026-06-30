import pytest

from preview import parse_range


@pytest.mark.parametrize("hdr,size,exp", [
    (None, 100, None),
    ("", 100, None),
    ("bytes=0-99", 100, (0, 99)),
    ("bytes=0-", 100, (0, 99)),
    ("bytes=50-", 100, (50, 99)),
    ("bytes=-20", 100, (80, 99)),     # suffix
    ("bytes=90-200", 100, (90, 99)),  # clamp end
    ("bytes=200-300", 100, None),     # unsatisfiable (start past end)
    ("bytes=abc", 100, None),
    ("bytes=-0", 100, None),          # zero-length suffix
    ("notbytes=0-1", 100, None),
])
def test_parse_range(hdr, size, exp):
    assert parse_range(hdr, size) == exp
