from oracle_zones import full_oracle_brands, audit_sample


def rows():
    r = []
    for i in range(1, 449):
        meth = "name-match" if i <= 200 else "leftover-only"
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": meth})
    for i in range(458, 900):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "original"})
    for i in range(475, 758):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "local"})
    for i in range(1263, 1276):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "uploaded"})
    for i in range(1200, 1240):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "karaoke-sourced"})
    return r


def test_full_oracle_is_namematch_and_leftover_only():
    got = full_oracle_brands(rows())
    assert got[0] == "NOMAD-0001" and got[-1] == "NOMAD-0448"
    assert len(got) == 448
    assert "NOMAD-0458" not in got            # marker era excluded


def test_audit_is_stratified_and_deterministic():
    a = audit_sample(rows(), per_label=7, seed=1729)
    b = audit_sample(rows(), per_label=7, seed=1729)
    assert a == b                              # deterministic
    methods = {"original", "local", "uploaded", "karaoke-sourced"}
    # every marker label present in input is represented
    seen = set()
    lut = {r["brand_code"]: r["method"] for r in rows()}
    for br in a:
        seen.add(lut[br])
    assert methods.issubset(seen)
    assert all(lut[br] != "name-match" for br in a)   # never audits the full-oracle zone


def test_audit_takes_all_uploaded_when_flagged():
    a = audit_sample(rows(), per_label=7, uploaded_all=True, seed=1729)
    lut = {r["brand_code"]: r["method"] for r in rows()}
    assert sum(1 for br in a if lut[br] == "uploaded") == 13
