def test_rapidfuzz_importable():
    from rapidfuzz import fuzz
    assert fuzz.WRatio("simon and garfunkel", "simon & garfunkel") > 80
