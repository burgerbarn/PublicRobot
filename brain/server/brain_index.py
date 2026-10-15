#!/usr/bin/env python3
"""Build the lightBot brain index: owner's code split by function/class, plus guide, results, fact cards, Q/A.

Sources (refreshed by sync_brain.sh from the Mac, plus Buddy's live voice scripts on this machine):
  ~/llm-train/data/code-v2/{rosorin-pro,buddy-app}   ~/llm-train/research/cloud   ~/voice/*.py *.sh   ~/llm-train/data/{facts,qa}
Output: ~/llm-train/brain/index.json
"""
import ast, json, os, re, time
from pathlib import Path

HOME = Path.home()
CODE_ROOTS = {"rosorin-pro": HOME / "llm-train/data/code-v2/rosorin-pro",
              "buddy-app": HOME / "llm-train/data/code-v2/buddy-app",
              "cloud": HOME / "llm-train/research/cloud"}   # pull_github.sh: brain/research of every claude/* branch
VOICE = HOME / "voice"
DATA = HOME / "llm-train/data"
OUT = HOME / "llm-train/brain/index.json"
CODE_EXT = {".py", ".sh", ".ts", ".tsx", ".js", ".cpp", ".hpp", ".h", ".c", ".yaml", ".yml", ".xml",
            ".launch", ".urdf", ".xacro", ".cmake", ".txt", ".json"}
MAX_CHARS = 3000      # one chunk; bigger functions/classes are windowed
WINDOW_LINES = 60


def windows(lines, start, label):
    out = []
    for i in range(0, len(lines), WINDOW_LINES - 10):
        part = lines[i:i + WINDOW_LINES]
        if part:
            out.append((start + i, start + i + len(part) - 1, label, "\n".join(part)))
        if i + WINDOW_LINES >= len(lines):
            break
    return out


def py_chunks(text):
    """Top-level functions and classes (methods of big classes separately); module code between them."""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return windows(lines, 1, "")
    out, covered = [], set()
    def add(node, label):
        a, b = node.lineno, node.end_lineno
        if node.decorator_list:
            a = min(d.lineno for d in node.decorator_list)
        body = "\n".join(lines[a - 1:b])
        if len(body) > MAX_CHARS:
            if isinstance(node, ast.ClassDef):
                head_end = min([n.lineno for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [b + 1]) - 1
                out.append((a, head_end, label, "\n".join(lines[a - 1:head_end])))
                for n in node.body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add(n, f"{label}.{n.name}")
            else:
                out.extend(windows(lines[a - 1:b], a, label))
        else:
            out.append((a, b, label, body))
        covered.update(range(a, b + 1))
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(n, f"def {n.name}")
        elif isinstance(n, ast.ClassDef):
            add(n, f"class {n.name}")
    rest, run = [], []
    for i, l in enumerate(lines, 1):           # module-level code (imports, constants, main)
        if i in covered:
            if run: rest.append(run); run = []
        else:
            run.append((i, l))
    if run: rest.append(run)
    for r in rest:
        body = "\n".join(l for _, l in r).strip()
        if body:
            out.extend(windows([l for _, l in r], r[0][0], "module"))
    return out


def md_chunks(text):
    out, cur, start, n = [], [], 1, 0
    for i, l in enumerate(text.splitlines(), 1):
        if re.match(r"#{1,4} ", l) and cur and sum(len(x) for x in cur) > 400:
            out.append((start, i - 1, cur[0][:80] if cur[0].startswith("#") else "", "\n".join(cur)))
            cur, start = [], i
        cur.append(l)
        if sum(len(x) for x in cur) > MAX_CHARS:
            out.append((start, i, cur[0][:80] if cur[0].startswith("#") else "", "\n".join(cur)))
            cur, start = [], i + 1
    if cur:
        out.append((start, start + len(cur) - 1, cur[0][:80] if cur[0].startswith("#") else "", "\n".join(cur)))
    return out


def add_file(chunks, repo, rel, path):
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return
    if not text.strip() or len(text) > 400_000:
        return
    ext = path.suffix.lower()
    kind = "doc" if ext == ".md" else "code"
    parts = md_chunks(text) if ext == ".md" else py_chunks(text) if ext == ".py" else windows(text.splitlines(), 1, "")
    for a, b, label, body in parts:
        if body.strip():
            chunks.append({"kind": kind, "src": f"{repo}/{rel}:{a}-{b}", "label": label, "text": body})


EMB_MODEL = "BAAI/bge-base-en-v1.5"
EMB_OUT = OUT.with_name("index_emb.npy")


def embed(chunks):
    """Meaning vectors for every chunk (semantic search next to BM25). GPU when free, else CPU."""
    try:
        import numpy as np, torch
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers not installed: BM25 only")
        return
    dev = "cuda" if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 2 * 2**30 else "cpu"
    m = SentenceTransformer(EMB_MODEL, device=dev)
    texts = [f"{c['label']} {c['src']}\n{c['text'][:2000]}" for c in chunks]
    v = m.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    np.save(EMB_OUT, v.astype("float16"))
    print(f"embedded {len(texts)} chunks on {dev} -> {EMB_OUT}")


def build():
    chunks = []
    for repo, root in CODE_ROOTS.items():
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and (p.suffix.lower() in CODE_EXT | {".md"}) and "package-lock" not in p.name:
                add_file(chunks, repo, str(p.relative_to(root)), p)
    for p in sorted(list(VOICE.glob("*.py")) + list(VOICE.glob("*.sh"))):
        add_file(chunks, "buddy-voice", p.name, p)
    for facts in sorted((DATA / "facts").glob("*.jsonl")):   # facts.jsonl (rebuild cards), research.jsonl (research agent)
        for l in open(facts):
            r = json.loads(l)
            chunks.append({"kind": "fact", "src": f'{r["id"]} ({r.get("source", "")})', "label": "",
                           "text": r["text"] + "\nKeywords: " + ", ".join(r.get("keywords", []))})
    qa = DATA / "qa/train.jsonl"
    if qa.exists():
        for l in open(qa):
            r = json.loads(l)
            chunks.append({"kind": "fact", "src": r.get("source", "qa"), "label": "",
                           "text": "Q: " + r["messages"][0]["content"] + "\nA: " + r["messages"][1]["content"]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "chunks": chunks}, open(OUT, "w"))
    embed(chunks)
    kinds = {}
    for c in chunks:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"indexed {len(chunks)} chunks {kinds} -> {OUT}")


if __name__ == "__main__":
    build()
