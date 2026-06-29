"""Tests for local_grouping: home unknown-brand local rotation-search results
into KJ-meaningful sections (4TB-SSD library by folder, YTDownloads by trust)."""

from local_grouping import (
    classify_local_file,
    GROUP_SORT_LIBRARY, GROUP_SORT_YT_COMMUNITY, GROUP_SORT_YT_UNVERIFIED,
)


def _g(path, filename, *, is_download, known=None, cfg=None):
    return classify_local_file(path, filename, cfg or {},
                               is_download=is_download,
                               known_community_brands=known or set())


class TestLibraryFolderGrouping:
    def test_active_folder_label(self):
        g = _g("/media/nomad/Nomad4TBOne/HyperMule/Master Karaoke Folder/"
               "Karaoke - Digital/Active/All Star Karaoke Digital Downloads/"
               "ASK-011000/ASK-011277 - 3 Doors Down - Kryptonite.zip",
               "ASK-011277 - 3 Doors Down - Kryptonite.zip", is_download=False)
        assert g["label"] == "Library — Karaoke - Digital/Active"
        assert g["key"] == "lib:Karaoke - Digital/Active"
        assert g["sort"] == GROUP_SORT_LIBRARY

    def test_dead_folder_label(self):
        g = _g("/media/nomad/Nomad4TBOne/HyperMule/Master Karaoke Folder/"
               "Karaoke - Digital/Dead/A-Major/AMAMS A-Major Ameri-Sing/"
               "AMAMS-002 - X - Y.zip", "AMAMS-002 - X - Y.zip",
               is_download=False)
        assert g["label"] == "Library — Karaoke - Digital/Dead"

    def test_two_library_folders_get_distinct_keys(self):
        a = _g("/x/Karaoke - Digital/Active/foo/f.zip", "f.zip", is_download=False)
        d = _g("/x/Karaoke - Digital/Dead/bar/f.zip", "f.zip", is_download=False)
        assert a["key"] != d["key"]

    def test_external_mount_is_stripped(self):
        cfg = {"external_media_mount": "/mnt/ssd"}
        g = _g("/mnt/ssd/Karaoke - Digital/Active/foo/f.zip", "f.zip",
               is_download=False, cfg=cfg)
        assert g["label"] == "Library — Karaoke - Digital/Active"

    def test_fallback_label_when_no_known_pattern(self):
        g = _g("/media/nomad/SomeCollection/Sub/file.zip", "file.zip",
               is_download=False)
        assert g["sort"] == GROUP_SORT_LIBRARY
        assert g["label"].startswith("Library — ")


class TestYTDownloadsTrustGrouping:
    def test_divebar_prefix_is_community(self):
        g = _g("/opt/nomad/YTDownloads/divebar__ESK - 3 Doors Down - Kryptonite.mp4",
               "divebar__ESK - 3 Doors Down - Kryptonite.mp4", is_download=True)
        assert g["label"] == "From YouTube — community brand"
        assert g["sort"] == GROUP_SORT_YT_COMMUNITY

    def test_registry_community_brand_is_community(self):
        # ObsKure is a registered community brand.
        g = _g("/opt/nomad/YTDownloads/abc123__ObsKure Karaoke__Queen - X.mp4",
               "abc123__ObsKure Karaoke__Queen - X.mp4", is_download=True)
        assert g["label"] == "From YouTube — community brand"

    def test_unregistered_brand_caught_via_cross_reference(self):
        # Espada Karaoke is not in the registry but appears as a community
        # brand in this search's KN results, so cross-ref marks it community.
        g = _g("/opt/nomad/YTDownloads/orwi3cX7iZU__Espada Karaoke__"
               "3 Doors Down - Kryptonite.mp4",
               "orwi3cX7iZU__Espada Karaoke__3 Doors Down - Kryptonite.mp4",
               is_download=True, known={"ESPADA KARAOKE", "ESK"})
        assert g["label"] == "From YouTube — community brand"

    def test_random_youtube_is_unverified(self):
        g = _g("/opt/nomad/YTDownloads/xyz789__Some Random Channel__Song.mp4",
               "xyz789__Some Random Channel__Song.mp4", is_download=True)
        assert g["label"] == "From YouTube — unverified"
        assert g["sort"] == GROUP_SORT_YT_UNVERIFIED

    def test_unparseable_filename_is_unverified(self):
        g = _g("/opt/nomad/YTDownloads/just-a-title.mp4", "just-a-title.mp4",
               is_download=True)
        assert g["label"] == "From YouTube — unverified"


class TestSortOrdering:
    def test_library_before_yt_community_before_unverified(self):
        assert GROUP_SORT_LIBRARY < GROUP_SORT_YT_COMMUNITY < GROUP_SORT_YT_UNVERIFIED
