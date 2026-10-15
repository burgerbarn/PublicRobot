# Rescore eval_answers.jsonl with format-tolerant matching: case-fold, drop thousands separators,
# drop the space before %, unify dashes, collapse whitespace. Usage: score_norm.py FILE ANSWER_KEY...
import json, re, sys
def norm(s):
    s = s.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\s+%", "%", s)
    return re.sub(r"\s+", " ", s)
rows = [json.loads(l) for l in open(sys.argv[1])]
nc = [r for r in rows if r["source"] != "control"]
for k in sys.argv[2:]:
    full = lambda r: all(norm(f) in norm(r[k]) for f in r["expected_facts"])
    print(f"{k}: non-control {sum(map(full, nc))}/{len(nc)}  controls {sum(full(r) for r in rows if r['source']=='control')}/{len(rows)-len(nc)}")
