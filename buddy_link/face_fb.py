#!/usr/bin/env python3
"""Buddy's face on the robot's touchscreen, drawn straight into the kernel framebuffer (/dev/fb0): no X, no browser,
no desktop (owner 2026-10-06: "I don't want a fully bloated operating system just to run Buddy's face").
State comes from the face server on bigbuddy (face_server.py /events, Server-Sent Events):
  {"t":"state","state":"idle|listening|thinking|working|speaking","text":...}   {"t":"bands","bands":[20 x 0..1]}
Without the stream it shows the idle face, dimmed. ~20 frames/s, numpy only. Env: BUDDY_FACE_EVENTS, FB_DEV."""
import json, math, os, random, threading, time

import numpy as np
import requests

EVENTS = os.environ.get('BUDDY_FACE_EVENTS', 'http://192.168.1.111:8290/events')
FB = os.environ.get('FB_DEV', '/dev/fb0')
W, H = (int(v) for v in open('/sys/class/graphics/fb0/virtual_size').read().split(','))
BPP = int(open('/sys/class/graphics/fb0/bits_per_pixel').read())
STRIDE = int(open('/sys/class/graphics/fb0/stride').read()) if os.path.exists('/sys/class/graphics/fb0/stride') else W * BPP // 8
assert BPP == 32, f'{BPP} bpp framebuffer: this draws 32 bpp only'

COLORS = {                                   # (r, g, b) of the face line, per state
    'idle': (120, 170, 255), 'listening': (90, 230, 140), 'thinking': (255, 190, 70),
    'working': (255, 190, 70), 'speaking': (240, 240, 255), 'offline': (90, 90, 110)}


class Feed(threading.Thread):
    """Follows the face server's event stream; reconnects forever."""
    def __init__(self):
        super().__init__(daemon=True)
        self.state, self.text, self.bands, self.t_bands, self.online = 'idle', '', [0.0] * 20, 0.0, False

    def run(self):
        while True:
            try:
                with requests.get(EVENTS, stream=True, timeout=(5, 60)) as r:
                    self.online = True
                    for line in r.iter_lines():
                        if not line or not line.startswith(b'data: '):
                            continue
                        ev = json.loads(line[6:])
                        if ev.get('t') == 'state':
                            self.state, self.text = ev.get('state', 'idle'), ev.get('text', '') or ''
                        elif ev.get('t') == 'bands':
                            self.bands, self.t_bands = list(ev.get('bands', []))[:20], time.time()
            except Exception:
                pass
            self.online = False
            time.sleep(2.0)


def ellipse(cy, cx, ry, rx):
    yy, xx = np.ogrid[:H, :W]
    return (((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2) <= 1.0


EYE_L, EYE_R, EYE_Y = int(W * 0.33), int(W * 0.67), int(H * 0.38)
EYE_RY, EYE_RX = int(H * 0.11), int(W * 0.07)
MOUTH_Y, MOUTH_W = int(H * 0.74), int(W * 0.40)


def frame(feed, t, blink):
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0:3] = (12, 12, 18)                                     # background
    state = feed.state if feed.online else 'offline'
    r, g, b = COLORS.get(state, COLORS['idle'])
    col = (b, g, r)                                                  # fb is BGRX
    # eyes: open ellipses, a blink closes them to a line; the pupils drift a little when idle
    ry = max(2, int(EYE_RY * (0.08 if blink else 1.0)))
    dx = int(math.sin(t * 0.7) * EYE_RX * 0.25) if state in ('idle', 'offline') else 0
    for cx in (EYE_L, EYE_R):
        m = ellipse(EYE_Y, cx, ry, EYE_RX)
        img[m, 0:3] = col
        if not blink:
            img[ellipse(EYE_Y, cx + dx, int(ry * 0.5), int(EYE_RX * 0.45)), 0:3] = (18, 12, 12)
    # mouth
    live = time.time() - feed.t_bands < 0.4 and any(v > 0.02 for v in feed.bands)
    if live and state in ('speaking', 'listening', 'idle', 'working', 'thinking'):
        n = max(1, len(feed.bands)); bw = MOUTH_W // n
        x0 = W // 2 - MOUTH_W // 2
        for i, v in enumerate(feed.bands):
            hgt = int(4 + min(1.0, max(0.0, v)) * H * 0.18)
            img[MOUTH_Y - hgt // 2:MOUTH_Y + hgt // 2, x0 + i * bw + 2:x0 + (i + 1) * bw - 2, 0:3] = col
    elif state == 'listening':                                       # open mouth: a small ring
        img[ellipse(MOUTH_Y, W // 2, int(H * 0.06), int(W * 0.05)) & ~ellipse(MOUTH_Y, W // 2, int(H * 0.04), int(W * 0.035)), 0:3] = col
    elif state in ('thinking', 'working'):                           # three dots, the middle one pulsing
        for k, cx in enumerate((W // 2 - int(W * 0.06), W // 2, W // 2 + int(W * 0.06))):
            rr = int(H * 0.012 * (1.6 if (k == 1 and int(t * 2) % 2 == 0) else 1.0))
            img[ellipse(MOUTH_Y, cx, rr, rr), 0:3] = col
    else:                                                            # idle: a gentle line, slight smile
        x0 = W // 2 - MOUTH_W // 2
        xs = np.arange(MOUTH_W)
        ys = MOUTH_Y + (np.sin((xs / MOUTH_W) * math.pi) * -H * 0.025).astype(int)
        for d in range(-3, 4):
            img[ys + d, x0 + xs, 0:3] = col
    return img


def main():
    try:
        open('/sys/class/graphics/fbcon/cursor_blink', 'w').write('0')
    except OSError:
        pass
    feed = Feed(); feed.start()
    fb = open(FB, 'r+b', buffering=0)
    next_blink, blink_until, last = time.time() + 3.0, 0.0, None
    while True:
        t = time.time()
        if t >= next_blink:
            blink_until, next_blink = t + 0.12, t + random.uniform(2.5, 6.0)
        blink = t < blink_until
        img = frame(feed, t, blink)
        if STRIDE == W * 4:
            fb.seek(0); fb.write(img.tobytes())
        else:
            for y in range(H):
                fb.seek(y * STRIDE); fb.write(img[y].tobytes())
        time.sleep(0.05)


if __name__ == '__main__':
    main()
