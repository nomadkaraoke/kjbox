from oracle_report import sort_for_review


def test_review_order_puts_uncertain_first():
    rows = [
        {"brand": "A", "verdict": "confirmed", "confidence": "high", "margin_db": "30"},
        {"brand": "B", "verdict": "confirmed", "confidence": "low", "margin_db": "3"},
        {"brand": "C", "verdict": "no_source", "confidence": "none", "margin_db": ""},
        {"brand": "D", "verdict": "confirmed", "confidence": "low", "margin_db": "5"},
    ]
    order = [r["brand"] for r in sort_for_review(rows)]
    assert order[0] == "C"                 # no_source first
    assert order[1:3] == ["B", "D"]        # low-confidence by ascending margin
    assert order[-1] == "A"                # high-confidence last
