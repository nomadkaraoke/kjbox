from unittest.mock import patch, MagicMock
from gen_client import GenClient


def test_parse_titles_happy_path():
    c = GenClient("https://api.example.com", "tok")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"results": [{"id": "1", "artist": "Queen",
                                           "title": "Bohemian Rhapsody", "confidence": 0.9}]}
    resp.raise_for_status.return_value = None
    with patch("gen_client.requests.post", return_value=resp) as post:
        out = c.parse_titles([{"id": "1", "filename": "x.mp4"}])
    assert out[0]["artist"] == "Queen"
    args, kwargs = post.call_args
    assert args[0].endswith("/api/parse-karaoke-titles")
    assert kwargs["headers"]["X-Admin-Token"] == "tok"


def test_parse_titles_returns_none_on_error():
    c = GenClient("https://api.example.com", "tok")
    with patch("gen_client.requests.post", side_effect=Exception("offline")):
        assert c.parse_titles([{"id": "1", "filename": "x"}]) is None


def test_parse_titles_empty_items_returns_empty():
    c = GenClient("https://api.example.com", "tok")
    assert c.parse_titles([]) == []
