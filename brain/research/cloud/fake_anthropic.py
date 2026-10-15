#!/usr/bin/env python3
"""Stand-in for the Anthropic API (and an OpenAI-compatible local model) for claude_gate_test.sh. Sends nothing out.

  fake_anthropic.py PORT MODE      MODE: ok | credit | batch-errored | batch-ok-label | batch-ok-cards | batch-ok-synth
Every request is appended to ./hits.log (path and request fields without the messages).
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, MODE = int(sys.argv[1]), sys.argv[2]
IDS = []
CREDIT = {"type": "error", "error": {"type": "invalid_request_error",
                                     "message": "Your credit balance is too low to access the Anthropic API."}}


def message(model, text):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 1234, "output_tokens": 56}}


def batch():
    return {"id": "msgbatch_1", "type": "message_batch", "processing_status": "ended",
            "request_counts": {"processing": 0, "succeeded": 0, "errored": len(IDS), "canceled": 0, "expired": 0},
            "ended_at": "2026-10-15T18:00:00Z", "created_at": "2026-10-15T17:59:00Z", "expires_at": "2026-10-16T17:59:00Z",
            "archived_at": None, "cancel_initiated_at": None,
            "results_url": f"http://127.0.0.1:{PORT}/v1/messages/batches/msgbatch_1/results"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ct="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open("hits.log", "a") as f:
            f.write(self.path + " " + json.dumps({k: v for k, v in body.items() if k not in ("messages", "requests")})[:300] + "\n")
        if self.path.startswith("/v1/chat/completions"):       # the local model: always "not in the guide"
            return self.send(200, {"choices": [{"message": {"content": "Not in the guide: unknown. [x]"}, "finish_reason": "stop"}]})
        if MODE == "credit":
            return self.send(400, CREDIT)
        if self.path.startswith("/v1/messages/batches"):
            IDS[:] = [r["custom_id"] for r in body.get("requests", [])]
            return self.send(200, batch())
        fmt = body.get("output_config", {}).get("format")
        return self.send(200, message(body["model"], json.dumps({"verdict": "FAIL", "why": "stand-in: inactive"}) if fmt
                                      else "stand-in answer [ch05]"))

    def do_GET(self):
        if not self.path.split("?")[0].endswith("/results"):
            return self.send(200, batch())
        texts = {"batch-ok-label": {"gold": "PASS", "why": "stand-in"},
                 "batch-ok-cards": {"cards": [{"text": "stand-in card", "keywords": ["k"], "section": "s"}]},
                 "batch-ok-synth": {"variants": [{"rc": 3, "out": "inactive", "gold": "FAIL", "why": "stand-in"}]}}
        lines = []
        for i in IDS:
            if MODE in texts:
                res = {"type": "succeeded", "message": message("claude-opus-5-5", json.dumps(texts[MODE]))}
            else:
                res = {"type": "errored", "error": CREDIT}
            lines.append(json.dumps({"custom_id": i, "result": res}))
        self.send(200, ("\n".join(lines) + "\n").encode(), "application/binary")


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
