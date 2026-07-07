# test_oracle_assemble.py
from oracle_assemble import plan_copies

TORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"


def test_plan_copies_only_eligible_and_names_consistently():
    results = [
        {"brand": "NOMAD-0100", "verdict": "confirmed", "confidence": "high",
         "winner_rel": "NOMAD-0100 - Idlewild - Little Discourage/01 Little Discourage.flac",
         "winner_ext": "flac", "approved": ""},
        {"brand": "NOMAD-0018", "verdict": "no_source", "confidence": "none",
         "winner_rel": "", "winner_ext": "", "approved": ""},          # skip
        {"brand": "NOMAD-0007", "verdict": "confirmed", "confidence": "low",
         "winner_rel": "NOMAD-0007 - The Hush Sound - A Dark Congregation/02 A Dark Congregation.mp3",
         "winner_ext": "mp3", "approved": ""},                          # low + unapproved -> skip
        {"brand": "NOMAD-0008", "verdict": "confirmed", "confidence": "low",
         "winner_rel": "NOMAD-0008 - The Hush Sound - As You Cry/06 As You Cry.mp3",
         "winner_ext": "mp3", "approved": "y"},                         # low + approved -> include
    ]
    manifest = {
        "NOMAD-0100": {"artist": "Idlewild", "title": "Little Discourage"},
        "NOMAD-0008": {"artist": "The Hush Sound", "title": "As You Cry"},
    }
    plan = plan_copies(results, manifest, "/DEST")
    dsts = {src.split("/")[-1]: dst for src, dst in plan}
    assert plan == [
        (f"{TORG}/NOMAD-0100 - Idlewild - Little Discourage/01 Little Discourage.flac",
         "/DEST/NOMAD-0100 - Idlewild - Little Discourage.flac"),
        (f"{TORG}/NOMAD-0008 - The Hush Sound - As You Cry/06 As You Cry.mp3",
         "/DEST/NOMAD-0008 - The Hush Sound - As You Cry.mp3"),
    ]
