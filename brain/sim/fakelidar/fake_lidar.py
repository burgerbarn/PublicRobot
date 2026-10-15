#!/usr/bin/env python3
"""Fake COIN-D6 LiDAR for lightbot-sim (no hardware; a static simulated room).

Creates a pseudo-terminal and links it like the real robot's udev rule does: /dev/ttyUSB0 -> /dev/pts/N
(root:dialout 0660, like the CH340's ttyUSB0) and /dev/lidar -> ttyUSB0 (relative, so `ls -l /dev/lidar` prints
`/dev/lidar -> ttyUSB0`, as in guide chapter 14). It streams the YDLIDAR-TOF "intensity" packets the guide decodes
(ch14 Step 2), written independently of the guide's coin_d6.py, which is the code under test:

  AA 55 | CT | LSN | FSA u16 | LSA u16 | CS u16 | LSN x (S0 intensity, S1, S2)       (16-bit values little-endian)
  angle = (u16 >> 1) / 64 deg (bit 0 set, as in the capture); distance mm = (S2 << 6) | (S1 >> 2);
  CS = XOR of the words PH, CT|LSN<<8, FSA, LSA, then per sample S0 and (S2 << 8 | S1).

One revolution (10.02 per second, the guide's 10.020 Hz) = a revolution-start packet (CT 0xC9 = (100 << 1) | 1,
LSN 1, distance 0, at ~358.9 deg native) + 16 packets of 25 samples 0.8965 deg apart from ~359.5 deg, and in about
one revolution of five a single-sample CT 0x00 packet just before the start packet (as in the repo's capture
test/coin_d6_sample.bin). 1373 bytes per revolution: ~13.76 KB/s, ~27.5 KB in `timeout 2 cat` (guide: 27506-27599).
Bytes go out evenly over the revolution, so a capture starts wherever the stream is (mid-packet, like the robot).

Native angles run clockwise seen from above (guide ch14 Step 8); the LiDAR's 180 deg points forward, so a point at
robot-frame bearing b (counter-clockwise from forward) is at native angle 180 - b. Scene, robot frame (x forward,
y left; the LiDAR is 0.048 m ahead of base_link, ch12's static TF):
  - a 5 x 4 m room: walls at x = +3.0 / -2.0 and y = +1.8 / -2.2, with a 0.9 m doorway in the front wall
    (y 0.6..1.5) through which a far wall at x = 7.05 is seen (returns 7.1-7.7 m; the guide quotes 7.94 m);
  - a 0.4 x 0.4 m box centred at (1.2, -1.0);
  - the robot's own body, from the capture: self-returns at native 34.4-57.1 deg (54-91 mm) and 291.8-315.5 deg
    (52-107 mm), ~52 per revolution (guide: 51-54 below range_min 0.12);
  - no return (distance 0) at native 315.5..360..34.4 and 57.1-62.5 deg (the rear blind sector and the arm tower),
    plus a dropout at range edges (> 0.5 m jump) and 0.3 % random dropouts: ~300 of 400 samples finite (guide 298-303).
Distance noise 5 mm std; intensities multiples of 4; the low 2 bits of S1 random like the capture's.

Sends only while a program holds the port (opens/closes counted with inotify); when the last user closes it the
exclusive flag (TIOCEXCL, which a pty keeps and a real ttyUSB loses) is cleared and unread bytes are dropped.
SIGUSR1 = the USB device drops for --unplug-s (3 s; ch14 Step 9's unbind/bind): both links vanish, the pty closes
(the reader's read fails), then a new pty appears under the same names.
Files (default /tmp/fakelidar): events.jsonl (open/close/unplug), status.json (counters, every second and at exit).
"""
import argparse
import ctypes
import fcntl
import grp
import json
import math
import os
import random
import select
import signal
import struct
import sys
import termios
import time
import tty

HZ = 10.02
SAMPLES = 400                  # 16 packets x 25
PER_PKT = 25
STEP = 0.8965                  # deg between samples (capture: 22.42 deg per 25-sample packet)
LASER_X = 0.048                # base_link -> laser (guide ch12 / base.launch.py)
# self-return profiles from the capture (mm, in sample order), spread over their native sectors
SELF_A = (34.4, 57.1, [54, 66, 65, 72, 84, 81, 86, 91, 83, 85, 86, 83, 87, 86, 82, 80, 77, 73, 81, 81, 76, 70, 74,
                       79, 77, 87])
SELF_B = (291.8, 315.5, [59, 52, 55, 57, 57, 65, 64, 66, 71, 74, 84, 84, 93, 98, 98, 102, 100, 107, 107, 107, 106,
                         104, 103, 101, 85, 62])
BLIND = ((315.5, 360.0), (0.0, 34.4), (57.1, 62.5))


def walls():
    """Line segments of the scene in the robot frame, metres."""
    x0, x1, y0, y1 = -2.0, 3.0, -2.2, 1.8
    seg = [((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)),
           ((x1, y0), (x1, 0.6)), ((x1, 1.5), (x1, y1)),                       # front wall with a doorway
           ((7.05, -3.0), (7.05, 4.0)),                                          # far wall seen through the door
           ((x1, 0.6), (3.15, 0.6)), ((x1, 1.5), (3.15, 1.5))]                  # door jambs (wall thickness)
    bx, by, h = 1.2, -1.0, 0.2
    seg += [((bx - h, by - h), (bx + h, by - h)), ((bx + h, by - h), (bx + h, by + h)),
            ((bx + h, by + h), (bx - h, by + h)), ((bx - h, by + h), (bx - h, by - h))]
    return seg


def cast(seg, bearing_deg):
    """Distance (m) from the LiDAR to the nearest segment along a robot-frame bearing; None = nothing."""
    dx, dy = math.cos(math.radians(bearing_deg)), math.sin(math.radians(bearing_deg))
    best = None
    for (ax, ay), (bx, by) in seg:
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = ax - LASER_X, ay
        t = (wx * ey - wy * ex) / den          # along the ray
        u = (wx * dy - wy * dx) / den          # along the segment
        if t > 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return best


def crc_words(pkt):
    """XOR checksum of the packet with CS = 0 (the YDLIDAR 'intensity' rule)."""
    w = lambda i: pkt[i] | (pkt[i + 1] << 8)
    x = w(0) ^ w(2) ^ w(4) ^ w(6)
    for k in range(pkt[3]):
        o = 10 + 3 * k
        x ^= pkt[o] ^ w(o + 1)
    return x


def packet(ct, fsa, lsa, samples):
    """samples: [(intensity, mm, low2)] -> bytes"""
    enc = lambda a: ((int(round((a % 360.0) * 64)) % 23040) << 1) | 1
    b = bytearray(b'\xaa\x55') + bytes([ct, len(samples)]) + struct.pack('<HHH', enc(fsa), enc(lsa), 0)
    for inten, mm, low in samples:
        mm = max(0, min(16383, int(mm)))
        b += bytes([inten & 0xFC, ((mm & 0x3F) << 2) | (low & 3), mm >> 6])
    struct.pack_into('<H', b, 8, crc_words(b))
    return bytes(b)


def in_sector(a, lo, hi):
    return lo <= a < hi


class Scene:
    def __init__(self, rng, noise_mm=5.0, dropout=0.003):
        self.rng, self.noise, self.dropout = rng, noise_mm, dropout
        self.seg = walls()
        self.true = {}                         # native angle (0.01 deg) -> true room distance, cached

    def room_mm(self, native):
        key = int(round(native * 100)) % 36000
        if key not in self.true:
            d = cast(self.seg, (180.0 - native) % 360.0)
            self.true[key] = 0.0 if d is None else d * 1000.0
        return self.true[key]

    def sample(self, native, prev_true):
        """-> (intensity, mm, low2, true_mm for the edge test)"""
        r = self.rng
        for lo, hi, prof in (SELF_A, SELF_B):
            if in_sector(native, lo, hi):
                f = (native - lo) / (hi - lo) * (len(prof) - 1)
                i = int(f)
                mm = prof[i] + (prof[min(i + 1, len(prof) - 1)] - prof[i]) * (f - i) + r.gauss(0, 3)
                return 4 * r.randint(4, 63), max(40, mm), r.choice((1, 2)), None
        if any(in_sector(native, lo, hi) for lo, hi in BLIND):
            return 4 * r.randint(6, 55), 0, r.choice((2, 3, 3)), None
        t = self.room_mm(native)
        if t <= 0 or t > 16000 or r.random() < self.dropout or (prev_true and abs(t - prev_true) > 500):
            return 4 * r.randint(0, 50), 0, r.choice((2, 3, 3)), t
        inten = int(max(4, min(252, 236 - 18 * t / 1000.0 + r.gauss(0, 30))))
        return inten, t + r.gauss(0, self.noise), r.randint(0, 3), t

    def revolution(self, hz):
        """-> bytes of one revolution: start packet, 16 x 25 samples, sometimes a single sample before the next start"""
        r = self.rng
        theta0 = 359.5 + r.uniform(-0.05, 0.35)
        ct_start = ((int(round(hz * 10)) & 0x7F) << 1) | 1
        out = [packet(ct_start, theta0 - 0.6, theta0 - 0.6, [(4 * r.randint(15, 25), 0, 3)])]
        prev_true = None
        for p in range(SAMPLES // PER_PKT):
            angs = [(theta0 + (p * PER_PKT + k) * STEP) % 360.0 for k in range(PER_PKT)]
            smp = []
            for a in angs:
                inten, mm, low, prev_true = self.sample(a, prev_true)
                smp.append((inten, mm, low))
            out.append(packet(0x00, angs[0], angs[-1], smp))
        if r.random() < 0.2:
            a = (theta0 + SAMPLES * STEP) % 360.0
            out.append(packet(0x00, a, a, [(4 * r.randint(15, 30), 0, 3)]))
        return b''.join(out)


_libc = ctypes.CDLL('libc.so.6', use_errno=True)
IN_OPEN, IN_CLOSE = 0x20, 0x08 | 0x10


class Lidar:
    def __init__(self, a):
        self.a = a
        os.makedirs(a.dir, exist_ok=True)
        self.ev_log = open(os.path.join(a.dir, 'events.jsonl'), 'a', buffering=1)
        self.users, self.connected, self.unplug_req = 0, False, False
        self.plug()
        self.rng = random.Random(a.seed)
        self.scene = Scene(self.rng, a.noise_mm)
        self.st = {'pts': self.pts, 'link': a.link, 'tty_link': a.tty_link, 'started': time.time(), 'hz': a.hz,
                   'opens': 0, 'closes': 0, 'revolutions': 0, 'revolutions_sent': 0, 'tx_bytes': 0,
                   'tx_dropped_bytes': 0, 'rx_bytes_ignored': 0,
                   'scene': 'room x -2.0..3.0 y -2.2..1.8 m, doorway y 0.6..1.5 in the front wall, far wall x 7.05, '
                            'box 0.4 m at (1.2, -1.0); self-returns native 34.4-57.1 and 291.8-315.5 deg'}
        self.event('start', pts=self.pts)

    def plug(self):
        """A new pty (the USB device enumerates): /dev/ttyUSB0 -> /dev/pts/N, /dev/lidar -> ttyUSB0."""
        a = self.a
        self.master, slave = os.openpty()
        tty.setraw(slave)
        t = termios.tcgetattr(slave)
        t[4] = t[5] = termios.B230400
        termios.tcsetattr(slave, termios.TCSANOW, t)
        self.pts = os.ttyname(slave)
        try:
            os.chown(self.pts, 0, grp.getgrnam('dialout').gr_gid)
        except (KeyError, PermissionError):
            pass
        os.chmod(self.pts, 0o660)
        for link, target in ((a.tty_link, self.pts), (a.link, os.path.basename(a.tty_link))):
            tmp = link + '.fakelidar-tmp'
            if os.path.lexists(tmp):
                os.unlink(tmp)
            os.symlink(target, tmp)
            os.replace(tmp, link)
        self.slave = slave                     # kept open: clears TIOCEXCL after the last user (see fake_board.py)
        self.ino = _libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.ino < 0 or _libc.inotify_add_watch(self.ino, self.pts.encode(), IN_OPEN | IN_CLOSE) < 0:
            raise OSError(ctypes.get_errno(), 'inotify on ' + self.pts)
        os.set_blocking(self.master, False)

    def unplug(self, seconds):
        """SIGUSR1: the USB device disappears for `seconds` (ch14 Step 9's unbind/bind): both links go, the pty is
        closed (a reader holding it gets an error / end of file), then a new pty appears under the same names."""
        a = self.a
        self.event('unplug', pts=self.pts, users=self.users)
        for link in (a.link, a.tty_link):
            if os.path.lexists(link):
                os.unlink(link)
        for fd in (self.master, self.slave, self.ino):
            try:
                os.close(fd)
            except OSError:
                pass
        self.users, self.connected = 0, False
        time.sleep(seconds)
        self.plug()
        self.st['unplugs'] = self.st.get('unplugs', 0) + 1
        self.st['pts'] = self.pts
        self.event('plug', pts=self.pts)

    def event(self, what, **kw):
        self.ev_log.write(json.dumps(dict(t=round(time.time(), 3), event=what, **kw)) + '\n')

    def write_status(self):
        st = dict(self.st, connected=self.connected, users=self.users, t=time.time())
        tmp = os.path.join(self.a.dir, 'status.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, os.path.join(self.a.dir, 'status.json'))

    def port_events(self):
        try:
            data = os.read(self.ino, 4096)
        except BlockingIOError:
            return
        i = 0
        while i + 16 <= len(data):
            _, mask, _, n = struct.unpack_from('iIII', data, i)
            i += 16 + n
            if mask & IN_OPEN:
                self.users += 1
                self.st['opens'] += 1
                self.event('open', users=self.users)
            elif mask & IN_CLOSE:
                self.users = max(0, self.users - 1)
                self.st['closes'] += 1
                self.event('close', users=self.users)
                if self.users == 0:
                    fcntl.ioctl(self.slave, termios.TIOCNXCL)
                    termios.tcflush(self.slave, termios.TCIOFLUSH)
        self.connected = self.users > 0

    def send(self, data):
        try:
            n = os.write(self.master, data)
        except (BlockingIOError, OSError):
            n = 0
        self.st['tx_bytes'] += n
        self.st['tx_dropped_bytes'] += len(data) - n

    def run(self):
        poll = select.poll()
        poll.register(self.master, select.POLLIN)
        poll.register(self.ino, select.POLLIN)
        T = 1.0 / self.a.hz
        t_rev = time.monotonic()
        rev, sent, rev_had_user = self.scene.revolution(self.a.hz), 0, False
        t_stat = t_rev
        while True:
            if self.unplug_req:
                self.unplug_req = False
                self.unplug(self.a.unplug_s)
                poll = select.poll()
                poll.register(self.master, select.POLLIN)
                poll.register(self.ino, select.POLLIN)
            ev = dict(poll.poll(self.a.tick_ms if self.connected else 200))
            was_connected = self.connected
            if ev.get(self.ino):
                self.port_events()
            if ev.get(self.master):
                try:                                   # the reader opens read-only; anything written is ignored
                    self.st['rx_bytes_ignored'] += len(os.read(self.master, 4096))
                except OSError:
                    pass
            # bytes that were due while nobody held the port are gone: a new reader gets the stream from "now"
            # (mid-packet), never a burst of old bytes
            live = self.connected and was_connected
            now = time.monotonic()
            while now >= t_rev + T:                    # the turntable keeps turning whether anybody listens or not
                if live and sent < len(rev):
                    self.send(rev[sent:])
                self.st['revolutions'] += 1
                self.st['revolutions_sent'] += rev_had_user
                t_rev += T
                if now - t_rev > 1.0:                  # long idle: skip whole revolutions, keep the phase
                    t_rev += math.floor((now - t_rev) / T) * T
                rev, sent, rev_had_user = self.scene.revolution(self.a.hz), 0, False
            due = int(len(rev) * (now - t_rev) / T)
            if live and due > sent:
                self.send(rev[sent:due])
            if self.connected:
                rev_had_user = True
            sent = max(sent, due)
            if now - t_stat >= 1.0:
                self.write_status()
                t_stat = now


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--link', default='/dev/lidar')
    ap.add_argument('--tty-link', default='/dev/ttyUSB0')
    ap.add_argument('--dir', default='/tmp/fakelidar')
    ap.add_argument('--hz', type=float, default=HZ)
    ap.add_argument('--noise-mm', type=float, default=5.0)
    ap.add_argument('--tick-ms', type=float, default=2.0)
    ap.add_argument('--seed', type=int, default=20260926)
    ap.add_argument('--unplug-s', type=float, default=3.0, help='SIGUSR1 = USB drop for this long (default 3 s)')
    ap.add_argument('--dump', metavar='FILE', help='write N revolutions (--revs) to FILE and exit (no pty)')
    ap.add_argument('--revs', type=int, default=30)
    a = ap.parse_args()
    if a.dump:
        sc = Scene(random.Random(a.seed), a.noise_mm)
        with open(a.dump, 'wb') as f:
            for _ in range(a.revs):
                f.write(sc.revolution(a.hz))
        return
    li = Lidar(a)

    def bye(*_):
        li.event('stop')
        li.write_status()
        try:
            for link, target in ((a.link, os.path.basename(a.tty_link)), (a.tty_link, li.pts)):
                if os.path.islink(link) and os.readlink(link) == target:
                    os.unlink(link)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGUSR1, lambda *_: setattr(li, 'unplug_req', True))
    print(f'fake lidar on {li.pts} -> {a.tty_link} <- {a.link}', flush=True)
    li.run()


if __name__ == '__main__':
    main()
