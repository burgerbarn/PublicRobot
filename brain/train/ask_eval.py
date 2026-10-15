# Score robot_ask.py (its own index and prompt) on eval.jsonl and eval2.jsonl, format-tolerant matching.
import json, os, re, sys, time
from pathlib import Path
sys.path.insert(0, os.path.expanduser("~/llm-train/ask"))
import robot_ask

OUT = Path(os.environ.get("OUT", "out-ask1")); OUT.mkdir(exist_ok=True)
def norm(s):
    s = s.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\s+%", "%", s)
    return re.sub(r"\s+", " ", s)

idx = robot_ask.load()
print(f"DATA chunks={len(idx.chunks)}", flush=True)
for e in ("eval2", "eval"):
    rows, secs = [], []
    for l in open(robot_ask.DATA / "qa" / f"{e}.jsonl"):
        r = json.loads(l)
        t = time.time()
        a, top, usage = robot_ask.ask(r["question"], idx)
        secs.append(time.time() - t)
        hits = [f for f in r["expected_facts"] if norm(f) in norm(a)]
        rows.append({"id": r["id"], "source": r.get("source"), "full": len(hits) == len(r["expected_facts"]),
                     "hits": hits, "expected_facts": r["expected_facts"], "seconds": round(secs[-1], 1),
                     "facts_in_retrieved": any(all(norm(f) in norm(c) for f in r["expected_facts"]) for _, c in top),
                     "retrieved": [s for s, _ in top], "usage": usage, "answer": a})
    with open(OUT / f"{e}.jsonl", "w") as f:
        for row in rows: f.write(json.dumps(row) + "\n")
    nc = [r for r in rows if r["source"] != "control"]; ctl = [r for r in rows if r["source"] == "control"]
    secs.sort()
    print(f"RUN {e}: non-control full={sum(r['full'] for r in nc)}/{len(nc)} "
          f"facts_in_retrieved={sum(r['facts_in_retrieved'] for r in nc)}/{len(nc)} "
          f"controls={sum(r['full'] for r in ctl)}/{len(ctl)} seconds median={secs[len(secs)//2]:.0f} max={secs[-1]:.0f}", flush=True)
