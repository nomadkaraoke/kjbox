# Vocals separation (guide dataset generation)

Generates the **isolated-vocals** guide dataset — the cleanest guide source (pure
original vocals over the karaoke instrumental, no doubled-band muddiness) — by
running audio-separator on each original-mix input and syncing the vocals stem to
the KJ device.

Runs on the Mac (Apple Silicon / MPS); the vocals then live on the device at
`/opt/nomad/downloads/NOMAD-vocals/NOMAD-#### - Artist - Title.flac`, resolvable by
brand code exactly like the audio and video.

## Run / resume

```bash
bash scripts/original_vocals/vocals/run.sh      # run or resume (keeps Mac awake)
bash scripts/original_vocals/vocals/status.sh   # progress, in another terminal
```

- **Resumable**: progress is the presence of vocals files on the device, so
  Ctrl-C anytime and re-run to continue — nothing is lost.
- **~90-120 s/track** on M3 Max (single model `vocals_mel_band_roformer.ckpt`);
  ~1.7 days for all 1,372. If the Mac sleeps it pauses; re-run to resume.
- Model + host are overridable via env (`MODEL`, `HOST`, `SRC`, `DST`).

## Weak-vocals flagging (catch wrong input files)

Phase-1's classifier occasionally picked an already-separated **instrumental**
instead of the original full mix (e.g. an unlabeled `Artist - Title.mp3` that's
actually instrumental). Those separate to near-silent vocals. `separate_vocals.sh`
records each vocals stem's peak loudness + size to `vocals_diagnostics.csv`, and:

```bash
python3 scripts/original_vocals/vocals/flag_weak_vocals.py
```

ranks the suspiciously-quiet ones (→ `weak_vocals_review.csv`) so the input file
can be re-checked/re-selected. Sync verification can't catch these — an
instrumental input correlates *even better* with the (instrumental) video — so
this energy check is the safety net.

## Files

| File | Purpose |
|------|---------|
| `separate_vocals.sh` | pull → separate → measure → push, resumable + diagnostics |
| `run.sh` / `status.sh` | one-command run/resume (caffeinated) / progress |
| `flag_weak_vocals.py` | flag minimal-vocals tracks (likely wrong input) |

`vocals_diagnostics.csv` / `weak_vocals_review.csv` are generated (git-ignored).
