#!/usr/bin/env python3
"""Stand-in for the robot API (chapter 22) for test_lightbot_robot.sh: token check, the GET paths, POST /stop.
Logs every request to ./api.log.   fake_robot_api.py PORT TOKEN"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
PORT, TOKEN = int(sys.argv[1]), sys.argv[2]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def answer(self, code, obj):
        open("api.log", "a").write(f"{self.command} {self.path} {code}\n")
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def handle_any(self):
        if self.headers.get("X-Robot-Token") != TOKEN:
            return self.answer(401, {"error": "missing or wrong X-Robot-Token"})
        if self.command == "GET" and self.path == "/status":
            return self.answer(200, {"battery_v": 12.6, "plugged": False, "wheels": False, "estop": None, "arm": "rest", "mind": "sleep"})
        if self.command == "GET" and self.path == "/frame.jpg":
            return self.answer(200, b"\xff\xd8stand-in-jpeg\xff\xd9")
        if self.command == "GET" and self.path in ("/state", "/task", "/seen", "/objects", "/selfcare"):
            return self.answer(200, {"path": self.path})
        if self.command == "POST" and self.path == "/stop":
            return self.answer(200, {"ok": True, "stopped": []})
        return self.answer(404, {"error": "not found"})
    do_GET = do_POST = handle_any
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
