#!/usr/bin/env python3
"""lightBot brain: an OpenAI-compatible endpoint that adds retrieved code/guide/fact excerpts to every chat.

  GET  /v1/models             models this server answers for (see MODELS)
  POST /v1/chat/completions   OpenAI chat API; the last user message is the search query (or "lightbot_query"); streaming passes through
  GET  /search?q=...&k=8      the retrieved chunks as JSON (for scripts and the guide runner)
  GET  /health

Backends: per model, in ~/llm-train/brain/models.json (default: LM Studio on localhost:1234). Auth: "Authorization: Bearer <key>", key in ~/llm-train/brain/key.
Index: ~/llm-train/brain/index.json (brain_index.py); reloaded when the file changes.
"""
import json, math, os, re, sys, threading, time, urllib.request, urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
INDEX = HOME / "llm-train/brain/index.json"
KEY = (HOME / "llm-train/brain/key").read_text().strip()
LMS = os.environ.get("BRAIN_BACKEND", "http://localhost:1234")
PORT = int(os.environ.get("BRAIN_PORT", "8300"))
HOST = os.environ.get("BRAIN_HOST", "127.0.0.1")   # localhost only; from the Mac use an SSH tunnel
K = int(os.environ.get("BRAIN_K", "8"))
CTX_CHARS = int(os.environ.get("BRAIN_CTX_CHARS", "14000"))   # ~3.5k tokens of excerpts
# Served name -> {"url": any OpenAI-compatible server (LM Studio, llama.cpp, Ollama; NVIDIA, Intel, AMD or Apple),
# "model": that server's model id}. Edit ~/llm-train/brain/models.json to move a model to another machine.
MODELS_FILE = HOME / "llm-train/brain/models.json"
DEFAULT_MODELS = {
    "lightbot": {"url": LMS, "model": "google/gemma-4-12b"},
    "lightbot-coder": {"url": LMS, "model": "rosorin-coder-7b-v2@q4_k_m"},
    "lightbot-oss": {"url": LMS, "model": "openai/gpt-oss-20b"},
}
def models():
    try:
        return json.load(open(MODELS_FILE))
    except (OSError, ValueError):
        return DEFAULT_MODELS

RULES = """You are the lightBot project's assistant: the Hiwonder ROSOrin Pro robot (ROS 2 Humble on a Jetson Orin), \
its build guide, its rebuild results, and the Buddy voice assistant on bigbuddy.
Excerpts from the owner's code, the guide, the rebuild results and fact cards follow, each with its source.
- Prefer the excerpts. Use exact names, paths, topics, endpoints, numbers and commands from them; do not invent APIs.
- When two excerpts describe similar things (old vs new partition, two rename directions, before vs after a fix), say which applies.
- If the excerpts do not cover the question, say so and answer from general knowledge, marked "Not in the project:".
- Never present a partition, flashing, boot or other destructive command as safe to run without checking it against the guide.
- Cite the sources you used as [source]."""


def terms(s):
    out = []
    for w in re.findall(r"[A-Za-z0-9_./:-]+", s):
        lw = w.lower()
        out.append(lw)
        parts = re.split(r"[_./:-]+|(?<=[a-z])(?=[A-Z])", w)
        if len(parts) > 1:
            out += [p.lower() for p in parts if len(p) > 1]
    return out


class Index:
    def __init__(self):
        self.mtime, self.lock = 0, threading.Lock()
        self.load()

    def load(self):
        m = INDEX.stat().st_mtime
        if m == self.mtime:
            return
        data = json.load(open(INDEX))
        chunks = data["chunks"]
        docs = [Counter(terms(c["label"] + " " + c["src"] + " " + c["text"])) for c in chunks]
        lens = [sum(d.values()) for d in docs]
        df = Counter(t for d in docs for t in d)
        with self.lock:
            self.chunks, self.docs, self.lens, self.df = chunks, docs, lens, df
            self.avg, self.built, self.mtime = sum(lens) / len(lens), data.get("built"), m
        self.load_emb(len(chunks))

    def load_emb(self, n):
        """Semantic vectors from brain_index.py (index_emb.npy); BM25 only when missing or out of date."""
        self.emb = None
        try:
            import numpy as np
            p = INDEX.with_name("index_emb.npy")
            if p.exists() and p.stat().st_mtime >= INDEX.stat().st_mtime - 600:
                v = np.load(p).astype("float32")
                if len(v) == n:
                    if not hasattr(self, "model"):
                        from sentence_transformers import SentenceTransformer
                        self.model = SentenceTransformer("BAAI/bge-base-en-v1.5", device="cpu")
                    self.emb = v
        except Exception as e:
            sys.stderr.write(f"semantic index not loaded: {e}\n")

    def search(self, q, k=K, k1=1.5, b=0.75):
        self.load()
        with self.lock:
            N, qt = len(self.docs), set(terms(q))
            idf = {t: math.log(1 + (N - self.df[t] + .5) / (self.df[t] + .5)) for t in qt if t in self.df}
            scores = []
            for i, d in enumerate(self.docs):
                s = 0.0
                for t, w in idf.items():
                    f = d.get(t)
                    if f:
                        s += w * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / self.avg))
                if s:
                    scores.append((s, i))
            scores.sort(reverse=True)
            if self.emb is not None:      # hybrid: reciprocal rank fusion of BM25 and semantic ranks
                import numpy as np
                qv = self.model.encode(["Represent this sentence for searching relevant passages: " + q],
                                       normalize_embeddings=True)[0]
                sem = np.argsort(-(self.emb @ qv))[:60]
                rrf = {}
                for r, (_, i) in enumerate(scores[:60]):
                    rrf[i] = rrf.get(i, 0) + 1 / (60 + r)
                for r, i in enumerate(sem):
                    rrf[int(i)] = rrf.get(int(i), 0) + 1 / (60 + r)
                scores = sorted(((v, i) for i, v in rrf.items()), reverse=True)
            out, used = [], 0
            for s, i in scores[:k]:
                c = self.chunks[i]
                if used + len(c["text"]) > CTX_CHARS and out:
                    break
                used += len(c["text"])
                out.append({**c, "score": round(s, 2)})
            return out


IDX = Index()


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def augment(body):
    msgs = body.get("messages", [])
    users = [text_of(m.get("content")) for m in msgs if m.get("role") == "user"]
    query = body.pop("lightbot_query", None) or (users[-1] if users else "")[-4000:]   # callers may set the search query
    if body.pop("lightbot_retrieve", True) is False:      # e.g. a judge that must use only the output it is given
        return []
    hits = IDX.search(query) if query.strip() else []
    ctx = "\n\n".join(f"[{h['src']}{' ' + h['label'] if h['label'] else ''}]\n{h['text'].strip()}" for h in hits)
    sys_msg = RULES + ("\n\n" + ctx if ctx else "\n\n(No excerpts matched.)")
    rest = [m for m in msgs if m.get("role") != "system"]
    own = "\n\n".join(text_of(m.get("content")) for m in msgs if m.get("role") == "system")
    body["messages"] = [{"role": "system", "content": sys_msg + ("\n\n" + own if own else "")}] + rest
    return [h["src"] for h in hits]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def send_json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        if self.headers.get("Authorization", "") == f"Bearer {KEY}":
            return True
        self.send_json(401, {"error": {"message": "missing or wrong API key"}})
        return False

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/health":
            return self.send_json(200, {"ok": True, "chunks": len(IDX.chunks), "built": IDX.built,
                                        "semantic": IDX.emb is not None})
        if not self.authed():
            return
        if u.path == "/v1/models":
            return self.send_json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "lightbot", "backend": b["url"]} for m, b in models().items()]})
        if u.path == "/search":
            q = urllib.parse.parse_qs(u.query)
            return self.send_json(200, {"hits": IDX.search(q.get("q", [""])[0], int(q.get("k", [K])[0]))})
        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.authed():
            return
        if urllib.parse.urlparse(self.path).path != "/v1/chat/completions":
            return self.send_json(404, {"error": {"message": "not found"}})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        name = body.get("model", "lightbot")
        be = models().get(name, {"url": LMS, "model": name})
        body["model"] = be["model"]
        srcs = augment(body)
        self.log_message("chat %s -> %s %s stream=%s sources=%s", name, be["url"], body["model"], bool(body.get("stream")), ",".join(srcs[:4]))
        req = urllib.request.Request(be["url"] + "/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", **({"Authorization": "Bearer " + be["key"]} if be.get("key") else {})})
        try:
            r = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            return self.send_json(e.code, json.loads(e.read() or b"{}") or {"error": {"message": str(e)}})
        except OSError as e:
            return self.send_json(503, {"error": {"message": f"model server {be['url']} not reachable ({e}); is it running, or is a training run using that GPU?"}})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            for line in r:
                self.wfile.write(line)
                self.wfile.flush()
            return
        out = json.loads(r.read())
        out["model"] = name
        out["lightbot_sources"] = srcs
        self.send_json(200, out)


if __name__ == "__main__":
    print(f"lightbot brain on {HOST}:{PORT}, {len(IDX.chunks)} chunks (built {IDX.built}), models {json.dumps(models())}", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
