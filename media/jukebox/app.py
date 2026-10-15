import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

app = Flask(__name__, static_folder="static", static_url_path="")

PLEX_URL = os.environ["PLEX_URL"].rstrip("/")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]
SECTIONS = [s.strip() for s in os.environ.get("PLEX_MUSIC_SECTIONS", "19,9").split(",")]


def plex_get(path, params=None):
    # [bigbuddy copy] token in a header, never in the URL: request/HTTPError messages and logs can't leak it
    r = requests.get(f"{PLEX_URL}{path}", params=dict(params or {}),
                     headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}, timeout=15)
    r.raise_for_status()
    return r.json().get("MediaContainer", {})


def track_out(t):
    part = (t.get("Media") or [{}])[0].get("Part", [{}])[0]
    part_key = part.get("key", "")
    art_path = t.get("thumb") or t.get("parentThumb")
    return {
        "key": t.get("key"),
        "title": t.get("title", "Untitled"),
        "artist": t.get("grandparentTitle", ""),
        "album": t.get("parentTitle", ""),
        "album_key": t.get("parentRatingKey"),
        "track_index": t.get("index"),
        "stream_url": f"/api/stream?path={quote(part_key, safe='')}" if part_key else None,
        "art_url": f"/api/art?path={quote(art_path, safe='')}" if art_path else None,
    }


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/init")
def init():
    total = 0
    for sec in SECTIONS:
        mc = plex_get(f"/library/sections/{sec}/all", {"type": 10, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
        total += mc.get("totalSize", 0)
    return jsonify({"total_tracks": total})


@app.get("/api/browse")
def browse():
    # Plex's default library order groups tracks by album (a "5 consecutive
    # tracks" window is usually one whole album), so even small chunks read
    # as clustered/"not random". Fetch each track independently (size=1 at
    # its own random offset) so there's no within-request grouping at all —
    # in parallel, since 40+ sequential requests would be visibly slow.
    picks_per_section = 20

    def fetch_one(sec, total):
        start = random.randint(0, max(0, total - 1))
        mc = plex_get(f"/library/sections/{sec}/all", {"type": 10, "X-Plex-Container-Start": start, "X-Plex-Container-Size": 1})
        items = mc.get("Metadata", [])
        return track_out(items[0]) if items else None

    tracks = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = []
        for sec in SECTIONS:
            mc = plex_get(f"/library/sections/{sec}/all", {"type": 10, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
            total = mc.get("totalSize", 0)
            futures += [pool.submit(fetch_one, sec, total) for _ in range(picks_per_section)]
        tracks = [f.result() for f in futures if f.result()]
    random.shuffle(tracks)
    return jsonify({"tracks": tracks})


def _norm(s):
    s = (s or "").lower().strip()
    return s[4:] if s.startswith("the ") else s


def artist_tracks(q, limit=200):
    """[bigbuddy copy] Tracks of artists whose name matches q ("the beatles" == "beatles"), shuffled."""
    out = []
    for sec in SECTIONS:
        mc = plex_get(f"/library/sections/{sec}/search", {"type": 8, "query": q, "X-Plex-Container-Start": 0,
                                                            "X-Plex-Container-Size": 10})
        for a in mc.get("Metadata", []):
            if _norm(a.get("title")) != _norm(q):
                continue
            leaves = plex_get(f"/library/metadata/{a['ratingKey']}/allLeaves")
            out += [track_out(t) for t in leaves.get("Metadata", [])]
    random.shuffle(out)
    return out[:limit]


def _key(s):
    """[bigbuddy copy] Loose name key for album/playlist matching: lowercase, alphanumerics only, no leading 'the'."""
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return s[4:] if s.startswith("the ") else s


def album_tracks(q):
    """[bigbuddy copy] Album whose title matches q (or 'ALBUM by ARTIST'), tracks in track order; [] if none."""
    i = q.lower().rfind(" by ")
    name, artist = (q[:i], q[i + 4:]) if i > 0 else (q, "")
    for sec in SECTIONS:
        mc = plex_get(f"/library/sections/{sec}/search", {"type": 9, "query": name, "X-Plex-Container-Start": 0,
                                                            "X-Plex-Container-Size": 20})
        for a in mc.get("Metadata", []):
            if _key(a.get("title")) != _key(name):
                continue
            if artist and _key(a.get("parentTitle")) != _key(artist):
                continue
            kids = plex_get(f"/library/metadata/{a['ratingKey']}/children")
            tracks = [track_out(t) for t in kids.get("Metadata", [])]
            tracks.sort(key=lambda t: (t.get("track_index") is None, t.get("track_index")))
            return tracks
    return []


def audio_playlists():
    """[bigbuddy copy] [(title, ratingKey, track count)] of the server's audio playlists. Plex keeps one set of its
    automatic playlists per music library, so names repeat (some copies empty)."""
    mc = plex_get("/playlists", {"playlistType": "audio"})
    return [(p.get("title", ""), p.get("ratingKey"), p.get("leafCount") or 0) for p in mc.get("Metadata", [])]


def playlist_tracks(q):
    """[bigbuddy copy] Audio playlist whose title matches q, tracks in playlist order; [] if none."""
    same = [(n, key) for title, key, n in audio_playlists() if _key(title) == _key(q)]
    if not same:
        return []
    for _, key in sorted(same, reverse=True):             # duplicate names: largest copy first
        try:
            items = plex_get(f"/playlists/{key}/items")
        except requests.RequestException as e:            # Plex 500s on some automatic playlists
            app.logger.warning("playlist %s items failed: %s", key, getattr(e.response, "status_code", type(e).__name__))
            continue
        tracks = [track_out(t) for t in items.get("Metadata", []) if t.get("type") == "track"]
        if tracks:
            return tracks
    return []


@app.get("/api/playlists")
def playlists():
    """[bigbuddy copy] Audio playlist names (Buddy puts them in his play_music tool description)."""
    return jsonify({"playlists": list(dict.fromkeys(t for t, _, n in audio_playlists() if n))})   # unique, non-empty


@app.get("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return browse()
    # [bigbuddy copy] order: exact artist (shuffled), exact album (track order), exact playlist (playlist order),
    # then title matches. A trailing "playlist" / "album" word forces that kind.
    low = q.lower()
    if low.endswith(" playlist"):
        return jsonify({"tracks": playlist_tracks(q[:-9].strip())})
    if low.endswith(" album"):
        return jsonify({"tracks": album_tracks(q[:-6].strip())})
    tracks = artist_tracks(q)                 # [bigbuddy copy] exact artist match first, then title matches
    if not tracks:
        whole = album_tracks(q) or playlist_tracks(q)
        if whole:
            return jsonify({"tracks": whole})
    seen = {t["key"] for t in tracks}
    for sec in SECTIONS:
        mc = plex_get(
            f"/library/sections/{sec}/search",
            {"type": 10, "query": q, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 50},
        )
        tracks += [t for t in (track_out(x) for x in mc.get("Metadata", [])) if t["key"] not in seen]
    return jsonify({"tracks": tracks})


@app.get("/api/album")
def album():
    album_key = request.args.get("key", "")
    if not album_key.isdigit():
        return jsonify({"error": "invalid key"}), 400
    # Plex's /children endpoint on an album returns its tracks already in
    # proper track-number order
    mc = plex_get(f"/library/metadata/{album_key}/children")
    tracks = [track_out(t) for t in mc.get("Metadata", [])]
    tracks.sort(key=lambda t: (t.get("track_index") is None, t.get("track_index")))
    return jsonify({"tracks": tracks})


@app.get("/api/shuffle")
def shuffle():
    sec = random.choice(SECTIONS)
    mc = plex_get(f"/library/sections/{sec}/all", {"type": 10, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
    total = mc.get("totalSize", 0)
    start = random.randint(0, max(0, total - 1))
    mc = plex_get(f"/library/sections/{sec}/all", {"type": 10, "X-Plex-Container-Start": start, "X-Plex-Container-Size": 1})
    items = mc.get("Metadata", [])
    if not items:
        return jsonify({"track": None})
    return jsonify({"track": track_out(items[0])})


@app.get("/api/stream")
def stream():
    part_key = unquote(request.args.get("path", ""))
    if not part_key.startswith("/library/parts/"):
        return "invalid path", 400

    headers = {}
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    upstream = requests.get(
        f"{PLEX_URL}{part_key}",
        headers={**headers, "X-Plex-Token": PLEX_TOKEN},   # [bigbuddy copy] header, not URL
        stream=True,
        timeout=30,
    )

    resp_headers = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        status=upstream.status_code,
        headers=resp_headers,
    )


@app.get("/api/art")
def art():
    img_path = unquote(request.args.get("path", ""))
    if not img_path.startswith("/library/"):
        return "invalid path", 400

    upstream = requests.get(
        f"{PLEX_URL}{img_path}",
        headers={"X-Plex-Token": PLEX_TOKEN},                # [bigbuddy copy] header, not URL
        timeout=15,
    )
    return Response(
        upstream.content,
        status=upstream.status_code,
        headers={
            "Content-Type": upstream.headers.get("Content-Type", "image/jpeg"),
            "Cache-Control": "public, max-age=3600",
        },
    )


# ---- [bigbuddy copy] remote control for Buddy: commands queued here, the page polls and executes them ----
_cmds, _cmd_id, _state, _lock = [], 0, {}, threading.Lock()
_BOOT = f"{time.time():.0f}"                  # page resets its command cursor when this changes (restart)


@app.post("/api/remote")
def remote_push():
    global _cmd_id
    body = request.get_json(force=True, silent=True) or {}
    if body.get("cmd") not in {"play", "shuffle", "pause", "resume", "next", "prev", "stop", "fullscreen"}:
        return jsonify({"error": "unknown cmd"}), 400
    with _lock:
        _cmd_id += 1
        _cmds.append({"id": _cmd_id, "t": time.time(), "cmd": body["cmd"], "arg": body.get("arg")})
        del _cmds[:-20]
    return jsonify({"id": _cmd_id})


@app.get("/api/remote/poll")
def remote_poll():
    after = int(request.args.get("after", 0))
    with _lock:
        fresh = [c for c in _cmds if c["id"] > after and time.time() - c["t"] < 30]
        return jsonify({"cmds": fresh, "last": _cmd_id, "boot": _BOOT})


@app.route("/api/remote/state", methods=["GET", "POST"])
def remote_state():
    if request.method == "POST":
        _state.clear(); _state.update(request.get_json(force=True, silent=True) or {}); _state["t"] = time.time()
        return jsonify({"ok": True})
    return jsonify(_state)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
