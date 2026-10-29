"""Fake karaoke-gen on :8010 that replays what gen answered Anya on 2026-10-22."""
import json
import sys
import time
from flask import Flask, jsonify, request

app = Flask(__name__)
SEARCH = json.load(open(sys.argv[1]))
used_sessions = set()

JUDGE = {
    ("I Will Be Okay", "fast"): {"alternatives": [], "canonical_artist": "Helluva Boss", "canonical_title": "I Will Be Okay",
                                 "confident": False, "engine": "catalog", "kind": "none", "needs_ai": True, "reason": "needs ai"},
    ("I Will Be Okay", "full"): {"alternatives": [], "canonical_artist": "Helluva Boss", "canonical_title": "You Will Be Okay",
                                 "confident": True, "engine": "ai", "kind": "content", "needs_ai": False,
                                 "reason": "The song from the series Helluva Boss (sung by Stolas / Bryce Pinkham) is titled 'You Will Be Okay'."},
    ("Anything", "fast"): {"alternatives": [], "canonical_artist": "Catfish and the Bottlemen", "canonical_title": "Anything",
                           "confident": True, "engine": "catalog", "kind": "none", "needs_ai": False, "reason": "already canonical"},
}


@app.post("/api/kjbox/auth/send-code")
def send_code():
    return jsonify({"status": "sent"})


@app.post("/api/kjbox/auth/verify-code")
def verify_code():
    return jsonify({"session_token": "fake-session", "email": request.json.get("email")})


@app.post("/api/kjbox/auth/<provider>")
def social(provider):
    return jsonify({"session_token": "fake-session", "user": {"email": "anya@example.com"},
                    "credits_granted": 0, "credit_status": "already_granted"})


@app.get("/api/users/me")
def me():
    return jsonify({"user": {"email": "anya@example.com", "credits": 1}, "email": "anya@example.com", "credits": 1})


@app.post("/api/kjbox/credits/show-credit")
def credit():
    return jsonify({"granted": True, "credits": 1})


@app.post("/api/catalog/match-judge")
def judge():
    b = request.json
    time.sleep(0.5 if b["stage"] == "fast" else 6)  # prod: full judge answered ~19s after search
    v = JUDGE.get((b["title"], b["stage"]))
    if not v:
        v = {"alternatives": [], "canonical_artist": b["artist"], "canonical_title": b["title"], "confident": True,
             "engine": "catalog", "kind": "none", "needs_ai": False, "reason": "already canonical"}
    return jsonify(v)


@app.post("/api/audio-search/search-standalone")
def search():
    b = request.json
    time.sleep(3)
    r = dict(SEARCH.get(b["title"]) or {"results": [], "search_session_id": "none"})
    r["search_session_id"] = "ss-%d" % time.time_ns()   # fresh per search, like gen
    return jsonify(r)


@app.post("/api/jobs/create-from-search")
def create():
    sid = request.json["search_session_id"]
    time.sleep(2)
    time.sleep(3)  # prod: create-from-search took ~3s
    if sid in used_sessions:  # gen consumes the search session: a repeat create 404s
        return jsonify({"detail": "Search session not found"}), 404
    used_sessions.add(sid)
    return jsonify({"job_id": "fake%04d" % len(used_sessions)})


@app.route("/<path:p>", methods=["GET", "POST", "PUT", "PATCH"])
def anything(p):
    print("UNHANDLED", request.method, p, file=sys.stderr)
    return jsonify({}), 200


if __name__ == "__main__":
    app.run(port=8019)
