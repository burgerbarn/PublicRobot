import os
import threading
import time
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

app = Flask(__name__, static_folder="static", static_url_path="")

PLEX_URL = os.environ["PLEX_URL"].rstrip("/")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]
SECTIONS = [s.strip() for s in os.environ.get("PLEX_VIDEO_SECTIONS", "").split(",") if s.strip()]
CLIENT_ID = os.environ.get("PLEX_CLIENT_ID", "buddy-video-bigbuddy")   # [bigbuddy copy] distinct from the original app


def plex_get(path, params=None):
    # [bigbuddy copy] token in a header, never in the URL: request/HTTPError messages and logs can't leak it
    r = requests.get(f"{PLEX_URL}{path}", params=dict(params or {}),
                     headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}, timeout=15)
    r.raise_for_status()
    return r.json().get("MediaContainer", {})


def movie_out(m):
    return {
        "key": m.get("ratingKey"),
        "title": m.get("title", "Untitled"),
        "year": m.get("year"),
        "genre": (m.get("Genre") or [{}])[0].get("tag") if m.get("Genre") else None,
        "summary": (m.get("summary") or "")[:200],
        "duration_ms": m.get("duration", 0),
        "thumb": f"/api/plexproxy{m['thumb']}" if m.get("thumb") else None,
    }


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/init")
def init():
    total = 0
    for sec in SECTIONS:
        mc = plex_get(f"/library/sections/{sec}/all", {"type": 1, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
        total += mc.get("totalSize", 0)
    all_sections = {s.get("key"): s.get("title", "") for s in plex_get("/library/sections").get("Directory", [])}
    channels = [{"index": i, "id": sec, "title": all_sections.get(sec, sec)} for i, sec in enumerate(SECTIONS)]
    return jsonify({"total_movies": total, "channels": len(SECTIONS), "channel_list": channels})


@app.get("/api/browse")
def browse():
    picks_per_section = 16

    def fetch_one(sec, total):
        start = random.randint(0, max(0, total - 1))
        mc = plex_get(f"/library/sections/{sec}/all", {"type": 1, "X-Plex-Container-Start": start, "X-Plex-Container-Size": 1})
        items = mc.get("Metadata", [])
        return movie_out(items[0]) if items else None

    movies = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = []
        for sec in SECTIONS:
            mc = plex_get(f"/library/sections/{sec}/all", {"type": 1, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
            total = mc.get("totalSize", 0)
            futures += [pool.submit(fetch_one, sec, total) for _ in range(picks_per_section)]
        movies = [f.result() for f in futures if f.result()]
    random.shuffle(movies)
    return jsonify({"movies": movies})


@app.get("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return browse()
    movies = []
    for sec in SECTIONS:
        mc = plex_get(
            f"/library/sections/{sec}/search",
            {"type": 1, "query": q, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 50},
        )
        movies += [movie_out(m) for m in mc.get("Metadata", [])]
    return jsonify({"movies": movies})


@app.get("/api/random")
def random_pick():
    # channel surf: one lightweight random pick, used by NEXT.
    # ?channel=<index into SECTIONS> picks within that specific channel
    # (clicking the channel badge); no/invalid channel falls back to random.
    channel_idx = request.args.get("channel", "")
    explicit = channel_idx.isdigit() and int(channel_idx) < len(SECTIONS)
    order = [SECTIONS[int(channel_idx)]] if explicit else random.sample(SECTIONS, len(SECTIONS))
    items = []
    for sec in order:
        # [bigbuddy copy] a plain random pick skips short clips (phone videos, e.g. in "Family") and moves on to
        # another channel if one has only clips; an explicitly chosen channel may return clips.
        mc = plex_get(f"/library/sections/{sec}/all", {"type": 1, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1})
        total = mc.get("totalSize", 0)
        for _ in range(1 if explicit else 6):
            start = random.randint(0, max(0, total - 1))
            mc = plex_get(f"/library/sections/{sec}/all", {"type": 1, "X-Plex-Container-Start": start, "X-Plex-Container-Size": 1})
            items = mc.get("Metadata", [])
            if explicit or (items and (items[0].get("duration") or 0) >= 20 * 60 * 1000):
                return jsonify({"movie": movie_out(items[0]) if items else None})
    return jsonify({"movie": movie_out(items[0]) if items else None})


@app.get("/api/play")
def play():
    rating_key = request.args.get("key", "")
    if not rating_key.isdigit():
        return jsonify({"error": "invalid key"}), 400

    session_id = str(uuid.uuid4())
    manifest_path = "/video/:/transcode/universal/start.m3u8"
    manifest_qs = {
        "path": f"/library/metadata/{rating_key}",
        "mediaIndex": 0,
        "partIndex": 0,
        "protocol": "hls",
        "fastSeek": 1,
        "directPlay": 0,
        "directStream": 1,
        "subtitleSize": 100,
        "audioBoost": 100,
        "location": "lan",
        "maxVideoBitrate": 8000,
        "videoResolution": "1920x1080",
        "X-Plex-Session-Identifier": session_id,
        "X-Plex-Client-Identifier": CLIENT_ID,
        "X-Plex-Product": "Buddy Video",
        "X-Plex-Platform": "Chrome",
    }
    qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in manifest_qs.items())
    return jsonify({
        "manifest_url": f"/api/plexproxy{manifest_path}?{qs}",
        "session_id": session_id,
    })


@app.get("/api/stop")
def stop():
    session_id = request.args.get("session", "")
    try:
        plex_get("/video/:/transcode/universal/stop", {"session": session_id})
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/plexproxy/<path:subpath>")
def plexproxy(subpath):
    params = request.args.to_dict()
    params["X-Plex-Token"] = PLEX_TOKEN

    headers = {}
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    upstream = requests.get(f"{PLEX_URL}/{subpath}", params=params, headers=headers, stream=True, timeout=30)
    if upstream.status_code >= 400:                   # [bigbuddy copy] log Plex's reason (token never logged)
        safe = {k: v for k, v in params.items() if k != "X-Plex-Token"}
        app.logger.warning("plex %s %s %s: %s", upstream.status_code, subpath[-40:], safe, upstream.text[:300])

    resp_headers = {}
    for h in ("Content-Type", "Content-Range", "Accept-Ranges"):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        status=upstream.status_code,
        headers=resp_headers,
    )


# ---- [bigbuddy copy] remote control for Buddy: commands queued here, the page polls and executes them ----
_cmds, _cmd_id, _state, _lock = [], 0, {}, threading.Lock()
_BOOT = f"{time.time():.0f}"                  # page resets its command cursor when this changes (restart)
REMOTE_CMDS = {"play", "random", "pause", "resume", "stop", "style", "frame", "fullscreen"}


@app.post("/api/remote")
def remote_push():
    global _cmd_id
    body = request.get_json(force=True, silent=True) or {}
    if body.get("cmd") not in REMOTE_CMDS:
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
