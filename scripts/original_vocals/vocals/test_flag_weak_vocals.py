import csv, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _write(diag, rows):
    with open(diag, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "dest", "input_bytes", "vocals_bytes",
                    "vocals_max_db", "vocals_mean_db", "dur_s", "seconds"])
        w.writerows(rows)


def test_flags_by_mean_not_peak(tmp_path):
    diag = tmp_path / "d.csv"
    out = tmp_path / "review.csv"
    # NOMAD-0002: peak loud (-6.4) but mean silent (-41.5) -> MUST be flagged now
    _write(diag, [
        ["NOMAD-0002", "a.flac", 6000000, 214341, "-6.4", "-41.5", "169", "90"],
        ["NOMAD-9999", "b.flac", 6000000, 900000, "-4.0", "-22.0", "180", "90"],  # real, not flagged
    ])
    subprocess.run([sys.executable, os.path.join(HERE, "flag_weak_vocals.py"),
                    "--diag", str(diag), "--out", str(out)], check=True)
    flagged = {r["brand"] for r in csv.DictReader(open(out))}
    assert "NOMAD-0002" in flagged
    assert "NOMAD-9999" not in flagged
