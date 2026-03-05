"""Local dev server with mock rotation data for UI testing.

Usage: python dev_server.py
Opens at http://localhost:5555
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app


class MockRotation:
    """In-memory rotation for local UI testing."""

    def __init__(self):
        self._entries = [
            {"row_index": 2, "singer": "Andrew", "song_artist": "Maximo Park - Books From Boxes", "status": "Up Next", "notes": ""},
            {"row_index": 3, "singer": "Lindsay", "song_artist": "FOB - Hallelujah", "status": "Waiting", "notes": ""},
            {"row_index": 4, "singer": "Greg", "song_artist": "Go Getter Greg", "status": "Waiting", "notes": ""},
            {"row_index": 5, "singer": "Andrew", "song_artist": "Panic! At the Disco - London Beckoned Songs About Money Written By Machines", "status": "Waiting", "notes": ""},
            {"row_index": 6, "singer": "Sarah", "song_artist": "Fleetwood Mac - Dreams", "status": "Waiting", "notes": "first timer"},
            {"row_index": 7, "singer": "Mike", "song_artist": "Journey - Don't Stop Believin", "status": "Being Made (!)", "notes": ""},
            {"row_index": 8, "singer": "Jen", "song_artist": "Adele - Rolling in the Deep", "status": "On Hold (BRB)", "notes": ""},
        ]
        self._next_row = 9

    def get_rotation(self, force_refresh=False):
        return [e for e in self._entries if e.get("status", "").lower() != "done"]

    def update_status(self, row_index, new_status):
        for e in self._entries:
            if e["row_index"] == row_index:
                e["status"] = new_status
                break

    def mark_singing(self, row_index):
        for e in self._entries:
            if e["status"].lower() in ("now singing", "singing now", "singing"):
                e["status"] = "Waiting"
            if e["row_index"] == row_index:
                e["status"] = "Now Singing"

    def mark_up_next(self, row_index):
        for e in self._entries:
            if e["status"].lower() in ("up next", "next"):
                e["status"] = "Waiting"
            if e["row_index"] == row_index:
                e["status"] = "Up Next"

    def add_entry(self, singer, song_artist, notes=""):
        self._entries.append({
            "row_index": self._next_row,
            "singer": singer,
            "song_artist": song_artist,
            "status": "Waiting",
            "notes": notes,
        })
        self._next_row += 1

    def update_entry(self, row_index, singer=None, song_artist=None):
        for e in self._entries:
            if e["row_index"] == row_index:
                if singer is not None:
                    e["singer"] = singer
                if song_artist is not None:
                    e["song_artist"] = song_artist
                break

    def delete_entry(self, row_index):
        self._entries = [e for e in self._entries if e["row_index"] != row_index]

    def archive_rotation(self):
        count = len(self._entries)
        self._entries = []
        return count


if __name__ == "__main__":
    app = create_app()
    app.rotation = MockRotation()
    print("Dev server with mock rotation at http://localhost:5555")
    app.run(host="127.0.0.1", port=5555, debug=True)
