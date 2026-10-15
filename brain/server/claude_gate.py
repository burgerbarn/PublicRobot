#!/usr/bin/env python3
"""Claude as the local system's last resort: only after the owner types "yes" at a terminal, every time.

The local tools (robot_ask.py, guide_check.py) answer with local models. When they cannot (model down, the answer
is not in the project, a FAIL or UNSURE check), they may OFFER to ask Claude through the Anthropic API. This module
is the only place that sends anything to Claude, and it sends nothing without permission():
  - it asks on the terminal (stdin and stderr must be a TTY); no terminal (cron, a timer, `ssh host cmd` without -t,
    a pipe) = no question = no request. There is no flag or environment variable that answers "yes" for the owner.
  - it says what would be sent, how much (characters, about how many tokens) and what it costs at list price.
  - every request is logged to ~/llm-train/brain/claude_calls.jsonl (time, tool, model, tokens, cost).
API key: ~/llm-train/brain/anthropic_key (chmod 600) or ANTHROPIC_API_KEY; the Python that runs the tool needs the
`anthropic` package. Without key, package or credit the call fails with one clear line (ClaudeError), never silently.

  python3 claude_gate.py --check      key, package and log location, without sending anything
"""
import json, os, sys, time
from pathlib import Path

HOME = Path.home()
KEYFILE = HOME / "llm-train/brain/anthropic_key"
LOG = HOME / "llm-train/brain/claude_calls.jsonl"
MODEL = os.environ.get("LIGHTBOT_CLAUDE_MODEL", "claude-opus-5-5")
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}  # $/M in, out


class ClaudeError(Exception):
    pass


def key():
    return os.environ.get("ANTHROPIC_API_KEY") or (KEYFILE.read_text().strip() if KEYFILE.exists() else "")


def status():
    """(ready, one line why). Sends nothing."""
    if not key():
        return False, f"no API key ({KEYFILE} or ANTHROPIC_API_KEY)"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, f"the Python running this ({sys.executable}) has no 'anthropic' package: {sys.executable} -m pip install anthropic"
    return True, f"key found, model {MODEL}, log {LOG}"


def cost(tin, tout, model=MODEL):
    pin, pout = PRICE.get(model, PRICE["claude-opus-5-5"])
    return tin / 1e6 * pin + tout / 1e6 * pout


def permission(reason, sends, chars, max_out=8000):
    """Ask the owner. True only for a typed "yes" at a terminal."""
    ready, why = status()
    if not ready:
        print(f"Claude not available: {why}", file=sys.stderr)
        return False
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        print("Claude not asked: no terminal to ask the owner on (run it in a terminal, e.g. `ssh -t`).", file=sys.stderr)
        return False
    tin = chars // 4 + 500
    print(f"\n{reason}\nAsk Claude ({MODEL}, Anthropic API, billed to the API key)? It sends {sends}: "
          f"{chars} characters, about {tin} tokens in, up to {max_out} out (thinking included), at most ${cost(tin, max_out):.2f}.",
          file=sys.stderr)
    print("Type yes to send, anything else to stop: ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip().lower() == "yes"
    except EOFError:
        return False


def ask(system, user, who, schema=None, max_tokens=8000, effort="medium"):
    """One request to Claude. Call only after permission() returned True. Returns the answer text."""
    ready, why = status()
    if not ready:
        raise ClaudeError(why)
    import anthropic
    client = anthropic.Anthropic(api_key=key())
    oc = {"effort": effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}
    t = time.time()
    try:
        m = client.beta.messages.create(model=MODEL, max_tokens=max_tokens, system=system, output_config=oc,
                                        messages=[{"role": "user", "content": user}],
                                        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.AuthenticationError:
        raise ClaudeError("the API key was refused (401): wrong or revoked key")
    except anthropic.PermissionDeniedError as e:
        raise ClaudeError(f"the API key may not use this (403): {e.message}")
    except anthropic.RateLimitError:
        raise ClaudeError("rate limited (429): try again later")
    except anthropic.BadRequestError as e:
        raise ClaudeError(("no API credit left: " if "credit" in e.message.lower() else "request refused (400): ") + e.message)
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        raise ClaudeError(f"Anthropic API not reachable: {e}")
    text = "".join(b.text for b in m.content if b.type == "text").strip()
    c = cost(m.usage.input_tokens, m.usage.output_tokens, m.model)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "who": who, "model": m.model,
                            "in": m.usage.input_tokens, "out": m.usage.output_tokens, "usd": round(c, 4),
                            "s": round(time.time() - t, 1), "stop": m.stop_reason}) + "\n")
    print(f"(Claude {m.model}: {m.usage.input_tokens} in, {m.usage.output_tokens} out, ${c:.3f}; logged to {LOG})",
          file=sys.stderr)
    if m.stop_reason == "refusal":
        raise ClaudeError("Claude declined to answer (refusal)")
    if not text:
        raise ClaudeError(f"empty answer (stop: {m.stop_reason})")
    return text


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        print(__doc__)
        sys.exit(2)
    ready, why = status()
    print(("ready: " if ready else "not ready: ") + why)
    sys.exit(0 if ready else 1)
