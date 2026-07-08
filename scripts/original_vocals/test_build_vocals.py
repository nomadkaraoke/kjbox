"""Tests for build_vocals.fullvocals_score — the reuse stem picker.

Full/main-vocals stems (incl. karaoke models, which still output the main vocal)
are accepted and ranked by quality; backing-only / no-vocals / instrumental /
non-audio are rejected (-1).
"""
from build_vocals import fullvocals_score


def test_prefers_bs_roformer_highest():
    assert fullvocals_score("Song (Vocals model_bs_roformer_ep_317_sdr_12.9755.ckpt).flac") == 100


def test_mel_band_incl_karaoke_is_accepted():
    # karaoke model still outputs the MAIN vocal — accepted (user confirmed)
    s = fullvocals_score("Song (Vocals mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt).flac")
    assert s == 90


def test_ranking_order():
    bs = fullvocals_score("x (Vocals model_bs_roformer_ep_317.ckpt).flac")
    mel = fullvocals_score("x (Vocals mel_band_roformer_x.ckpt).flac")
    mdx23c = fullvocals_score("x (Vocals MDX23C-8KFFT-InstVoc_HQ_2.ckpt).flac")
    ht = fullvocals_score("x (Vocals htdemucs_6s.yaml).flac")
    uvr = fullvocals_score("x (Vocals UVR_MDXNET_KARA_2).wav")
    filt = fullvocals_score("x (Filtered Vocals) (MDX v2.1-inst 496).flac")
    assert bs > mel > mdx23c > ht > uvr > filt > 0


def test_lead_vocals_accepted_as_default():
    assert fullvocals_score("Song (Lead Vocals).flac") == 50


def test_bare_vocals_accepted():
    assert fullvocals_score("Song (Vocals).mp3") == 50


def test_rejects_instrumental():
    assert fullvocals_score("Song (Instrumental model_bs_roformer.ckpt).flac") == -1


def test_rejects_no_vocals():
    assert fullvocals_score("Song (No Vocals).flac") == -1


def test_rejects_backing_vocals():
    assert fullvocals_score("Song (Back Vocals).flac") == -1
    assert fullvocals_score("Song (Backing Vocals).flac") == -1


def test_rejects_other_stems():
    for stem in ("Drums", "Bass", "Other", "Guitar", "Piano"):
        assert fullvocals_score(f"Song ({stem}).flac") == -1


def test_rejects_non_audio():
    assert fullvocals_score("Song (Vocals).txt") == -1
    assert fullvocals_score("Song (Vocals).png") == -1
