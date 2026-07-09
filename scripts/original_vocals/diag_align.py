"""DIAGNOSTIC (not shipped): for a spread of brands, measure
  O = original->video offset (what the pipeline applies to the guide), and
  L = guide-vs-original signed lag (0 if they share a timebase).
If L is consistently non-zero, the shipped offset O is wrong for the guide;
the correct guide delay is O + L.
Run ON the device with the kj-controller venv python."""
import glob, os, sys
import verify_sync as vs

AUD = "/opt/nomad/downloads/NOMAD-audio"
VID = "/opt/nomad/downloads/NOMAD-720p"
GUI = "/opt/nomad/downloads/NOMAD-vocals"
SR = vs.ANALYSIS_SR


def signed_guide_lag(orig, guide, max_lag_s=15.0):
    """Return (L_seconds, peak). L>0: guide is delayed vs original (guide[t]~=orig[t-L]).
    L<0: guide leads original. Searches both directions and takes the stronger peak."""
    ml = int(max_lag_s * SR)
    # forward: orig[t+lag] ~ guide[t]  => guide delayed by lag  => L = +lag
    lf, pf = vs.best_lag(orig, guide, ml)
    # reverse: guide[t+lag] ~ orig[t]  => guide leads by lag     => L = -lag
    lr, pr = vs.best_lag(guide, orig, ml)
    return (lf / SR, pf) if pf >= pr else (-lr / SR, pr)


def main(argv=None):
    brands = argv or sys.argv[1:]
    aidx = vs._index_by_brand(glob.glob(os.path.join(AUD, "*")))
    vidx = vs._index_by_brand(glob.glob(os.path.join(VID, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(GUI, "*")))
    print(f"{'brand':12s} {'O(orig->vid)':>13s} {'L(guide-orig)':>14s} {'Lpeak':>6s} {'O+L':>7s}")
    for b in brands:
        b = b.upper()
        if b not in aidx or b not in vidx or b not in gidx:
            print(f"{b:12s} MISSING"); continue
        try:
            sr = vs.verify_pair(vidx[b], aidx[b])
            orig = vs.decode_mono(aidx[b], dur=90)
            guide = vs.decode_mono(gidx[b], dur=90)
            L, Lpk = signed_guide_lag(orig, guide)
            print(f"{b:12s} {sr.offset_s:12.3f}s {L:13.3f}s {Lpk:6.2f} {sr.offset_s + L:6.3f}s")
        except Exception as e:
            print(f"{b:12s} ERROR {e}")


if __name__ == "__main__":
    main()
