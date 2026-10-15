#!/usr/bin/env python3
"""Send Buddy-style requests (buddy_voice.py's own SYSTEM, TOOLS and request settings) to an LLM endpoint and time them.
Reads SYSTEM/TOOLS from ~/voice/buddy_voice.py with ast (nothing in that file runs).
  buddy_llm_test.py URL [MODEL]
"""
import ast, json, os, sys, time, urllib.request

URL = sys.argv[1]
MODEL = sys.argv[2] if len(sys.argv) > 2 else "google/gemma-4-12b"
src = open(os.path.expanduser("~/voice/buddy_voice.py")).read()
tree = ast.parse(src)
SAFE_CALLS = {"_tool", "E", "dict", "list", "str", "int", "float", "tuple", "set", "expanduser", "get", "join", "format"}
def harmless(node):
    """Only literals, names and calls to the helpers above (no model loads, no I/O)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in SAFE_CALLS:
                return False
    return True

ns = {"os": os, "_PL": ""}                         # _PL: playlist names Buddy reads at startup
nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("_tool", "E"))
         or (isinstance(n, ast.Assign) and harmless(n.value))]
for _ in range(4):                                   # a few passes: later constants may use earlier ones
    for n in nodes:
        try:
            exec(ast.get_source_segment(src, n), ns)
        except Exception:
            pass
SYSTEM, TOOLS = ns["SYSTEM"], ns["TOOLS"]

PROMPTS = ["What's the capital of Australia?", "Tell me a short joke.", "What do you see in the room?",
           "Turn the Evo on.", "How are you today?"]
print(f"endpoint {URL} model {MODEL}, {len(TOOLS)} tools")
for q in PROMPTS:
    body = {"model": MODEL, "messages": [{"role": "system", "content": f"{SYSTEM} Current date and time: {time.strftime('%A %Y-%m-%d %H:%M:%S')}."},
                                         {"role": "user", "content": q}],
            "max_tokens": 250, "temperature": 0.7, "reasoning_effort": "none", "tools": TOOLS}
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(),
                                             {"Content-Type": "application/json"}), timeout=120))
    except Exception as e:
        print(f"  {q!r}: ERROR {e}"); continue
    m = r["choices"][0]["message"]
    calls = [c["function"]["name"] for c in (m.get("tool_calls") or [])]
    u = r.get("usage", {})
    print(f"  {time.time() - t:5.1f} s  in {u.get('prompt_tokens')} out {u.get('completion_tokens')}  "
          f"{q!r} -> {('TOOL ' + ','.join(calls)) if calls else (m.get('content') or '')[:90]!r}")
