#!/usr/bin/env python3
"""Fake Hiwonder RRC controller board for lightbot-sim (no hardware; nothing ever moves).

Creates a pseudo-terminal, links it as /dev/ttyACM0 (root:dialout 0660, like the real CDC-ACM port) and speaks the
board's framing protocol: 0xAA 0x55 | function | length | payload | CRC-8/MAXIM(function, length, payload).
This is an independent implementation (it does not import the guide's rrc_protocol.py: that is the code under test).

Board -> computer, only while a program has the port open (open/close of the pty counted with inotify):
  function 7 IMU      <6f ax ay az (g) gx gy gz (deg/s), ~110.5 per second. Means = the frame captured on the real
                      robot on 2026-09-26 (guide ch10: +0.0403 -0.0581 +0.9437 g, -1.9395 -2.4238 +0.0352 deg/s);
                      noise: gyro std 0.052/0.070/0.039 deg/s (the driver's calibration log, ch11), accel 0.002 g;
                      quantised like the real stream (accel 1/16384 g, gyro 1/1024 deg/s).
  function 8 gamepad  <HB4b, all zero (no controller input), 20 per second.
  function 0 system   04 + <H battery mV, 1 per second (12670 mV, the value the guide quotes).
  function 5 servo    replies to read queries [cmd, id] -> [id, cmd, ok=0, value] for the arm servos 1-5 and 10:
                      0x05 position <h, 0x07 supply <H mV, 0x09 temperature <B, 0x0D torque <b.
Computer -> board (recorded in rx.jsonl, never acted on physically):
  function 3 motor (sub 1: n, then <Bf per motor), function 2 buzzer <HHHH, function 5 servo
  (0x01 move <HB + n x <BH, 0x03 hold n ids, 0x0C torque off, reads above). Servo moves update the simulated
  position (linear over the commanded duration) so read-backs follow the commands.

Files (default dir /tmp/fakeboard): rx.jsonl (one line per received frame), events.jsonl (open/close of the port),
status.json (counters, rewritten every second and at exit).
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

FUNC_SYS, FUNC_BUZZER, FUNC_MOTOR, FUNC_SERVO, FUNC_KEY, FUNC_IMU, FUNC_PAD = 0, 2, 3, 5, 6, 7, 8
NAMES = {0: 'sys', 1: 'led', 2: 'buzzer', 3: 'motor', 4: 'pwm_servo', 5: 'bus_servo', 6: 'key', 7: 'imu',
         8: 'gamepad', 9: 'sbus', 10: 'oled'}
IMU_MEAN = (0.0403, -0.0581, 0.9437, -1.9395, -2.4238, 0.0352)
IMU_STD = (0.002, 0.002, 0.002, 0.052, 0.070, 0.039)
# arm read on the robot 2026-09-29, starting from the tuck (guide ch11 "First enable by hand"); torque off when tucked
TUCK = {1: 500, 2: 963, 3: 46, 4: 131, 5: 498, 10: 629}
TEMPS = {1: 33, 2: 42, 3: 38, 4: 35, 5: 31, 10: 30}


def crc8_maxim(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8C if c & 1 else c >> 1
    return c


def frame(func, payload):
    body = bytes([func, len(payload)]) + payload
    return b'\xaa\x55' + body + bytes([crc8_maxim(body)])


_libc = ctypes.CDLL('libc.so.6', use_errno=True)
IN_OPEN, IN_CLOSE = 0x20, 0x08 | 0x10          # IN_CLOSE_WRITE | IN_CLOSE_NOWRITE


class Parser:
    def __init__(self):
        self.buf, self.crc_errors = bytearray(), 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\xaa\x55')
            if i < 0:
                del self.buf[:-1]
                return out
            del self.buf[:i]
            if len(self.buf) < 5 or len(self.buf) < 5 + self.buf[3]:
                return out
            n = self.buf[3]
            body = bytes(self.buf[2:4 + n])
            if self.buf[4 + n] == crc8_maxim(body):
                out.append((body[0], body[2:]))
                del self.buf[:5 + n]
            else:
                self.crc_errors += 1
                del self.buf[:2]


class Servos:
    """Simulated bus-servo positions: a move goes linearly from where the servo is to the target in its duration."""

    def __init__(self, pose, torque):
        self.s = {sid: {'from': p, 'to': p, 't0': 0.0, 'dur': 0.0, 'torque': torque} for sid, p in pose.items()}

    def pos(self, sid, now):
        s = self.s[sid]
        if s['dur'] <= 0 or now >= s['t0'] + s['dur']:
            return s['to']
        return int(round(s['from'] + (s['to'] - s['from']) * (now - s['t0']) / s['dur']))

    def move(self, sid, target, dur, now):
        if sid in self.s:
            p = self.pos(sid, now)
            self.s[sid].update({'from': p, 'to': target, 't0': now, 'dur': dur, 'torque': 1})

    def hold(self, sid, now):
        if sid in self.s:
            p = self.pos(sid, now)
            self.s[sid].update({'from': p, 'to': p, 'dur': 0.0})

    def torque_off(self, sid, now):
        if sid in self.s:
            self.hold(sid, now)
            self.s[sid]['torque'] = 0


class Board:
    def __init__(self, a):
        self.a = a
        os.makedirs(a.dir, exist_ok=True)
        self.rx_log = open(os.path.join(a.dir, 'rx.jsonl'), 'a', buffering=1)
        self.ev_log = open(os.path.join(a.dir, 'events.jsonl'), 'a', buffering=1)
        self.master, slave = os.openpty()
        tty.setraw(slave)
        t = termios.tcgetattr(slave)
        t[4] = t[5] = termios.B1000000
        termios.tcsetattr(slave, termios.TCSANOW, t)
        self.pts = os.ttyname(slave)
        try:
            os.chown(self.pts, 0, grp.getgrnam('dialout').gr_gid)
        except (KeyError, PermissionError):
            pass
        os.chmod(self.pts, 0o660)
        tmp = a.link + '.fakeboard-tmp'
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink(self.pts, tmp)
        os.replace(tmp, a.link)
        # The board keeps its own slave fd: a pty slave keeps its TIOCEXCL flag after the last close (a real
        # CDC-ACM port is released and loses it), so the board clears it with TIOCNXCL on this fd whenever the last
        # user closes the port; it also flushes what no one read. Users are counted with inotify open/close events.
        self.slave = slave
        self.users = 0
        self.ino = _libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.ino < 0 or _libc.inotify_add_watch(self.ino, self.pts.encode(), IN_OPEN | IN_CLOSE) < 0:
            raise OSError(ctypes.get_errno(), 'inotify on ' + self.pts)
        os.set_blocking(self.master, False)
        self.parser = Parser()
        self.servos = Servos(TUCK, torque=0)
        self.rng = random.Random(a.seed)
        self.connected = False
        self.st = {'pts': self.pts, 'link': a.link, 'started': time.time(), 'opens': 0, 'closes': 0,
                   'tx_frames': {}, 'tx_bytes': 0, 'tx_dropped_bytes': 0, 'rx_frames': {}, 'rx_crc_errors': 0,
                   'motor_frames': 0, 'motor_nonzero_frames': 0, 'motor_max_abs_rps': 0.0, 'last_motor_rps': None,
                   'buzzer_frames': [], 'servo_moves': 0, 'servo_holds': 0, 'servo_torque_off': 0,
                   'servo_reads': {}, 'unknown_frames': 0}
        self.event('start', pts=self.pts)

    def event(self, what, **kw):
        self.ev_log.write(json.dumps(dict(t=round(time.time(), 3), event=what, **kw)) + '\n')

    def send(self, func, payload):
        f = frame(func, payload)
        try:
            n = os.write(self.master, f)
        except BlockingIOError:
            n = 0
        except OSError:
            n = 0
        self.st['tx_bytes'] += n
        self.st['tx_dropped_bytes'] += len(f) - n
        k = NAMES.get(func, str(func))
        self.st['tx_frames'][k] = self.st['tx_frames'].get(k, 0) + 1

    def imu(self):
        v = [self.rng.gauss(m, s) for m, s in zip(IMU_MEAN, IMU_STD)]
        v = [round(x * 16384) / 16384 for x in v[:3]] + [round(x * 1024) / 1024 for x in v[3:]]
        self.send(FUNC_IMU, struct.pack('<6f', *v))

    def handle(self, func, p, now):
        k = NAMES.get(func, str(func))
        self.st['rx_frames'][k] = self.st['rx_frames'].get(k, 0) + 1
        rec = {'t': round(time.time(), 3), 'func': func, 'name': k, 'hex': p.hex(' ')}
        if func == FUNC_MOTOR and len(p) >= 2 and p[0] == 0x01 and len(p) == 2 + 5 * p[1]:
            rps = {mid: round(sp, 4) for mid, sp in (struct.unpack_from('<Bf', p, 2 + 5 * i) for i in range(p[1]))}
            rec['motor_rps'] = rps
            self.st['motor_frames'] += 1
            self.st['last_motor_rps'] = rps
            m = max((abs(v) for v in rps.values()), default=0.0)
            if m > 0:
                self.st['motor_nonzero_frames'] += 1
            self.st['motor_max_abs_rps'] = max(self.st['motor_max_abs_rps'], m)
        elif func == FUNC_BUZZER and len(p) == 8:
            rec['buzzer'] = dict(zip(('freq_hz', 'on_ms', 'off_ms', 'repeat'), struct.unpack('<HHHH', p)))
            if len(self.st['buzzer_frames']) < 50:
                self.st['buzzer_frames'].append(rec['buzzer'])
        elif func == FUNC_SERVO and p:
            cmd = p[0]
            if cmd == 0x01 and len(p) >= 4:
                dur_ms, n = struct.unpack_from('<HB', p, 1)
                tg = {}
                for i in range(n):
                    if 4 + 3 * i + 3 <= len(p):
                        sid, pulse = struct.unpack_from('<BH', p, 4 + 3 * i)
                        tg[sid] = pulse
                        self.servos.move(sid, pulse, dur_ms / 1000.0, now)
                rec['servo_move'] = {'ms': dur_ms, 'targets': tg}
                self.st['servo_moves'] += 1
            elif cmd == 0x03 and len(p) >= 2:
                for sid in p[2:2 + p[1]]:
                    self.servos.hold(sid, now)
                self.st['servo_holds'] += 1
            elif cmd == 0x0C and len(p) == 2:
                self.servos.torque_off(p[1], now)
                self.st['servo_torque_off'] += 1
            elif cmd in (0x05, 0x07, 0x09, 0x0D) and len(p) == 2:
                sid = p[1]
                key = f'0x{cmd:02X}'
                self.st['servo_reads'][key] = self.st['servo_reads'].get(key, 0) + 1
                if sid in self.servos.s:                      # other ids: no servo on the bus, no reply
                    s = self.servos.s[sid]
                    val = {0x05: ('<h', self.servos.pos(sid, now)), 0x07: ('<H', 12600),
                           0x09: ('<B', TEMPS.get(sid, 35)), 0x0D: ('<b', s['torque'])}[cmd]
                    self.send(FUNC_SERVO, bytes([sid, cmd, 0]) + struct.pack(*val))
        else:
            self.st['unknown_frames'] += 1
        self.rx_log.write(json.dumps(rec) + '\n')

    def write_status(self):
        st = dict(self.st, connected=self.connected, rx_crc_errors=self.parser.crc_errors, t=time.time(),
                  servo_pos={sid: self.servos.pos(sid, time.monotonic()) for sid in self.servos.s},
                  servo_torque={sid: s['torque'] for sid, s in self.servos.s.items()})
        tmp = os.path.join(self.a.dir, 'status.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, os.path.join(self.a.dir, 'status.json'))

    def read_all(self, now):
        while True:
            try:
                data = os.read(self.master, 4096)
            except (BlockingIOError, OSError):          # EAGAIN, or EIO: nobody holds the port
                return
            if not data:
                return
            for func, p in self.parser.feed(data):
                self.handle(func, p, now)

    def port_events(self):
        """inotify open/close of the pts -> number of open file descriptions (users) of the port."""
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
                    fcntl.ioctl(self.slave, termios.TIOCNXCL)       # what a real port loses on its last close
                    termios.tcflush(self.slave, termios.TCIOFLUSH)  # unread stream: gone, like the real port
                    self.parser.buf.clear()                          # a half frame from the last user
        self.connected = self.users > 0

    def run(self):
        poll = select.poll()
        poll.register(self.master, select.POLLIN)
        poll.register(self.ino, select.POLLIN)
        imu_dt, pad_dt, bat_dt = 1.0 / self.a.imu_hz, 1.0 / self.a.pad_hz, 1.0 / self.a.bat_hz
        now = time.monotonic()
        t_imu, t_pad, t_bat, t_stat = now, now + 0.013, now + 0.37, now
        while True:
            now = time.monotonic()
            wait = max(0.0, min(t_imu, t_pad, t_bat) - now) if self.connected else 0.5
            ev = dict(poll.poll(wait * 1000.0))
            now = time.monotonic()
            was_connected = self.connected
            if ev.get(self.ino):
                self.port_events()
            if ev.get(self.master):
                self.read_all(now)
            if not was_connected:
                # nobody held the port: nothing was sent (the stream is not kept for a later reader); the board's
                # clocks kept running: skip the missed slots, keep the phase (also on the iteration that sees an
                # open, so a new reader does not get a burst of frames that were never due while it listened)
                t_imu, t_pad, t_bat = (t + math.ceil((now - t) / dt) * dt if t < now else t
                                       for t, dt in ((t_imu, imu_dt), (t_pad, pad_dt), (t_bat, bat_dt)))
            if self.connected:
                # after a stall (machine busy), at most 0.1 s of frames are sent late, not a long burst
                t_imu, t_pad, t_bat = max(t_imu, now - 0.1), max(t_pad, now - 0.1), max(t_bat, now - 0.1)
                while now >= t_imu:
                    self.imu()
                    t_imu += imu_dt
                if now >= t_pad:
                    self.send(FUNC_PAD, bytes(7))
                    t_pad += pad_dt
                if now >= t_bat:
                    self.send(FUNC_SYS, bytes([0x04]) + struct.pack('<H', self.a.battery_mv))
                    t_bat += bat_dt
            if now - t_stat >= 1.0:
                self.write_status()
                t_stat = now


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--link', default='/dev/ttyACM0')
    ap.add_argument('--dir', default='/tmp/fakeboard')
    ap.add_argument('--imu-hz', type=float, default=110.5)
    ap.add_argument('--pad-hz', type=float, default=20.0)
    ap.add_argument('--bat-hz', type=float, default=1.0)
    ap.add_argument('--battery-mv', type=int, default=12670)
    ap.add_argument('--seed', type=int, default=20260926)
    a = ap.parse_args()
    b = Board(a)

    def bye(*_):
        b.event('stop')
        b.write_status()
        try:
            if os.path.islink(a.link) and os.readlink(a.link) == b.pts:
                os.unlink(a.link)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    print(f'fake board on {b.pts} -> {a.link}', flush=True)
    b.run()


if __name__ == '__main__':
    main()
