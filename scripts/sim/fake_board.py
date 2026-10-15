#!/usr/bin/env python3
"""Emulated STM32 controller board for the simulated rebuild (owner 2026-10-08: "simulate this").

Speaks the protocol verified on the real board (docs/hardware.md, 2026-09-26) on a pseudo-terminal, so the guide's
own code (rrc_protocol.py, board_reader.py, board_driver.py) talks to it as it would to /dev/ttyACM0:
  frame  0xAA 0x55 | func | len | payload | CRC-8/MAXIM(func, len, payload)
  sends  func 0 battery (0x04 + uint16 mV) ~1 Hz, func 7 IMU (6 x float32: g, deg/s) ~110 Hz,
         func 8 gamepad (7 bytes, zero = no input) ~20 Hz
  takes  func 3 motor (0x01, n, (id u8, rps f32) x n) - no watchdog, like the real board: the last command stays
         func 5 bus servo: 0x01 move (ms u16, n, (id u8, pulse u16) x n), 0x03 stop, 0x0B/0x0C torque on/off,
                reads 0x05 pos, 0x07 vin, 0x09 temp, 0x0D torque -> reply (id, cmd, ok=0, value)
         func 2 buzzer (freq, on ms, off ms, repeat), func 1 LED: logged
Raw IMU = inverse of the robot's calibration (offset X +0.0460 Y -0.0635 Z -0.0501 g, scale 1.0058 0.9964 1.0076)
plus the measured gyro bias (-2.2, -2.4, -0.06) deg/s, so the driver's calibration path is exercised.
Wheel rps -> body velocity by the inverse of motion.mecanum_rps (wheel 0.08 m, wheelbase 0.17706, track 0.17165).

Outputs for the rest of the simulation:
  --link PATH    symlink to the pty (default /dev/ttyACM0), group dialout, mode 660
  --state PATH   JSON written 20x/s: body velocity, pose by integration, wheel rps, servo pulses, buzzer, counts
  --twist-udp H:P  also send the commanded body velocity as JSON over UDP (a Gazebo bridge drives the model with it)
Hands-on steps while it runs: UDP JSON to 127.0.0.1:5603 inside the robot (see control_loop): gamepad buttons and
sticks for N seconds, the board's key 1 (also `kill -USR1 <pid>`). Wrapper on the Mac: scripts/sim/hands.
"""
import argparse, fcntl, grp, json, math, os, pty, random, select, signal, socket, struct, termios, threading, time

FUNC_SYS, FUNC_LED, FUNC_BUZZER, FUNC_MOTOR, FUNC_BUS_SERVO, FUNC_KEY, FUNC_IMU, FUNC_GAMEPAD = 0, 1, 2, 3, 5, 6, 7, 8
OFFSET, SCALE = (0.0460, -0.0635, -0.0501), (1.0058, 0.9964, 1.0076)
GYRO_BIAS = (-2.2, -2.4, -0.06)
WHEEL_D, WHEELBASE, TRACK = 0.08, 0.17706, 0.17165
SERVO_INIT = {1: 500, 2: 860, 3: 45, 4: 178, 5: 500, 10: 499}   # power-on = where the arm rests: arm_poses.yaml 'default'


def crc8_maxim(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8C if c & 1 else c >> 1
    return c


def frame(func, payload):
    body = bytes([func, len(payload)]) + bytes(payload)
    return b'\xaa\x55' + body + bytes([crc8_maxim(body)])


def body_velocity(rps):
    """Inverse of motion.mecanum_rps: wheel rps (motors 1..4, factory signs) -> vx, vy, wz."""
    c = math.pi * WHEEL_D
    m1, m2, m3, m4 = rps[0] * c, rps[1] * c, -rps[2] * c, -rps[3] * c
    k = (WHEELBASE + TRACK) / 2.0
    vx = (m1 + m2 + m3 + m4) / 4.0
    vy = (-m1 + m2 + m3 - m4) / 4.0
    wz = (-m1 - m2 + m3 + m4) / (4.0 * k)
    return vx, vy, wz


class PortUsers:
    """Real USB serial (ttyACM/ttyUSB) forgets TIOCEXCL when its last user closes it and delivers nothing while closed.
    A pty does neither (measured in the simulated robot 2026-10-08: TIOCEXCL survives the last close, data queues up).
    Opens and closes of the port are counted with inotify (the emulator, root without CAP_SYS_PTRACE, cannot read
    other users' /proc/<pid>/fd). When the count drops to 0: clear the exclusive flag and drop what is queued. While
    nobody has it open, nothing is sent. While it is open, only whole frames that fit are written (a reader that falls
    behind loses frames, never gets a split one)."""
    IN_OPEN, IN_CLOSE_WRITE, IN_CLOSE_NOWRITE = 0x20, 0x08, 0x10

    def __init__(self, slave_fd, name, cap=3072):
        import ctypes
        self.fd, self.name, self.users, self.cap = slave_fd, os.path.realpath(name), 0, cap
        libc = ctypes.CDLL(None, use_errno=True)
        self.ino = libc.inotify_init()
        if self.ino < 0 or libc.inotify_add_watch(self.ino, self.name.encode(),
                                                  self.IN_OPEN | self.IN_CLOSE_WRITE | self.IN_CLOSE_NOWRITE) < 0:
            raise OSError(ctypes.get_errno(), 'inotify on ' + self.name)
        threading.Thread(target=self.watch, daemon=True).start()

    def count(self):
        return self.users

    def watch(self):
        while True:
            buf = os.read(self.ino, 4096)
            i = 0
            while i + 16 <= len(buf):
                wd, mask, cookie, ln = struct.unpack_from('iIII', buf, i)
                i += 16 + ln
                if mask & self.IN_OPEN:
                    self.users += 1
                elif mask & (self.IN_CLOSE_WRITE | self.IN_CLOSE_NOWRITE):
                    self.users = max(0, self.users - 1)
                    if self.users == 0:
                        fcntl.ioctl(self.fd, termios.TIOCNXCL)
                        termios.tcflush(self.fd, termios.TCIFLUSH)

    def room(self, nbytes):
        queued = struct.unpack('i', fcntl.ioctl(self.fd, termios.FIONREAD, b'\0\0\0\0'))[0]
        return queued + nbytes <= self.cap


class Board:
    def __init__(self, args):
        self.args = args
        self.master, self.slave_fd = pty.openpty()   # port settings stay at the tty defaults, like a fresh ttyACM0
        self.slave_name = os.ttyname(self.slave_fd)  # our slave fd stays open so the port never hangs up
        os.set_blocking(self.master, False)          # a full buffer drops bytes instead of stalling the board
        self.port = PortUsers(self.slave_fd, self.slave_name)
        self.lock = threading.Lock()
        self.rps = [0.0, 0.0, 0.0, 0.0]
        self.pose = [0.0, 0.0, 0.0]
        self.servo = dict(SERVO_INIT)
        self.servo_move = {}           # id -> (start pulse, target, t0, duration)
        self.torque = {sid: 1 for sid in SERVO_INIT}
        self.battery_mv = args.battery_mv
        self.counts = {'rx_frames': 0, 'crc_errors': 0, 'motor': 0, 'servo': 0, 'buzzer': 0, 'led': 0, 'unknown': 0}
        self.buzzer = None
        self.key_pending = False
        self.pad = (0, 0, (0, 0, 0, 0))         # buttons u16, hat u8, axes lx ly rx ry (int8): what the receiver reports
        self.pad_until = 0.0
        self.buf = bytearray()
        self.udp = None
        if args.twist_udp:
            h, p = args.twist_udp.rsplit(':', 1)
            self.udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (h, int(p)))
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, 'key_pending', True))

    def control_loop(self):
        """Hands for the simulation (UDP JSON on 127.0.0.1:5603): {"pad": {"buttons": 1, "hat": 0, "axes": [0, 100, 0, 0]},
        "for": 2.0} holds that gamepad state for 2 s, then the pad reads idle again; {"key": 1} presses the board's key 1."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 5603))
        while True:
            try:
                d = json.loads(sock.recv(4096))
            except ValueError:
                continue
            if 'pad' in d:
                p = d['pad']
                ax = [max(-127, min(127, int(v))) for v in (list(p.get('axes', [])) + [0, 0, 0, 0])[:4]]
                self.pad = (int(p.get('buttons', 0)) & 0xFFFF, int(p.get('hat', 0)) & 0xFF, tuple(ax))
                self.pad_until = time.monotonic() + float(d.get('for', 1.0))
            if d.get('key'):
                self.key_pending = True

    def link(self):
        if not self.args.link:
            return
        try:
            os.unlink(self.args.link)
        except FileNotFoundError:
            pass
        os.symlink(self.slave_name, self.args.link)
        try:
            os.chown(self.slave_name, -1, grp.getgrnam('dialout').gr_gid)
        except (KeyError, PermissionError):
            pass
        try:
            os.chmod(self.slave_name, 0o660)
        except PermissionError:
            pass

    def write(self, data):
        if not self.port.count() or not self.port.room(len(data)):
            return                     # port closed, or the reader is behind: a real board's bytes are lost too
        try:
            os.write(self.master, data)
        except OSError:
            pass

    # ---------- receiving
    def handle(self, func, p):
        self.counts['rx_frames'] += 1
        if func == FUNC_MOTOR and len(p) >= 2 and p[0] == 0x01:
            n = p[1]
            with self.lock:
                for i in range(n):
                    mid, r = struct.unpack_from('<Bf', p, 2 + 5 * i)
                    if 0 <= mid < 4:
                        self.rps[mid] = r
            self.counts['motor'] += 1
        elif func == FUNC_BUS_SERVO and p:
            self.counts['servo'] += 1
            sub = p[0]
            now = time.monotonic()
            with self.lock:
                if sub == 0x01 and len(p) >= 4:
                    ms, n = struct.unpack_from('<HB', p, 1)
                    for i in range(n):
                        sid, pulse = struct.unpack_from('<BH', p, 4 + 3 * i)
                        if sid in self.servo:      # a move command powers the servo again (torque on), as the real bus servos do
                            self.torque[sid] = 1
                            self.servo_move[sid] = (self.servo[sid], max(0, min(1000, pulse)), now, max(ms, 1) / 1000)
                elif sub == 0x03 and len(p) >= 2:
                    for sid in p[2:2 + p[1]]:
                        self.servo_move.pop(sid, None)
                elif sub in (0x0B, 0x0C) and len(p) >= 2:
                    self.torque[p[1]] = 1 if sub == 0x0B else 0
                    self.servo_move.pop(p[1], None)
                elif sub in (0x05, 0x07, 0x09, 0x0D) and len(p) >= 2 and p[1] in self.servo:
                    sid = p[1]
                    if sub == 0x05:
                        reply = struct.pack('<BBbh', sid, sub, 0, int(round(self.servo[sid])))
                    elif sub == 0x07:
                        reply = struct.pack('<BBbH', sid, sub, 0, self.battery_mv - 400)
                    elif sub == 0x09:
                        reply = struct.pack('<BBbB', sid, sub, 0, 38)
                    else:
                        reply = struct.pack('<BBbb', sid, sub, 0, self.torque[sid])
                    self.write(frame(FUNC_BUS_SERVO, reply))
        elif func == FUNC_BUZZER and len(p) == 8:
            self.buzzer = dict(zip(('freq', 'on_ms', 'off_ms', 'repeat'), struct.unpack('<HHHH', p)), t=time.time())
            self.counts['buzzer'] += 1
        elif func == FUNC_LED:
            self.counts['led'] += 1
        else:
            self.counts['unknown'] += 1

    def rx_loop(self):
        while True:
            r, _, _ = select.select([self.master], [], [], 0.02)
            if not r:
                self.buf.clear()          # 20 ms of silence ends a half-received frame (a real UART parser resets)
                continue
            try:
                data = os.read(self.master, 4096)
            except OSError:
                time.sleep(0.05)
                continue
            self.buf += data
            while True:
                i = self.buf.find(b'\xaa\x55')
                if i < 0:
                    del self.buf[:-1]
                    break
                del self.buf[:i]
                if len(self.buf) < 5:
                    break
                n = self.buf[3]
                if n > 64:                    # no command is that long: a false header, not a frame
                    self.counts['crc_errors'] += 1
                    del self.buf[:2]
                    continue
                if len(self.buf) < 5 + n:
                    break
                body, crc = bytes(self.buf[2:4 + n]), self.buf[4 + n]
                if crc == crc8_maxim(body):
                    self.handle(body[0], body[2:])
                    del self.buf[:5 + n]
                else:
                    self.counts['crc_errors'] += 1
                    del self.buf[:2]

    # ---------- sending + physics
    def run(self):
        threading.Thread(target=self.rx_loop, daemon=True).start()
        threading.Thread(target=self.control_loop, daemon=True).start()
        self.link()
        t0 = last = time.monotonic()
        next_imu = next_pad = next_bat = next_state = t0
        while True:
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                vx, vy, wz = body_velocity(self.rps)
                x, y, th = self.pose
                h = th + wz * dt / 2
                self.pose = [x + (vx * math.cos(h) - vy * math.sin(h)) * dt,
                             y + (vx * math.sin(h) + vy * math.cos(h)) * dt,
                             math.atan2(math.sin(th + wz * dt), math.cos(th + wz * dt))]
                for sid, (a, b, ts, dur) in list(self.servo_move.items()):
                    f = min(1.0, (now - ts) / dur)
                    self.servo[sid] = a + (b - a) * f
                    if f >= 1.0:
                        del self.servo_move[sid]
            if now >= next_imu:
                next_imu += 1 / 110.0
                g = (0.0, 0.0, 1.0)
                acc = [(g[i] / SCALE[i]) + OFFSET[i] + random.gauss(0, 0.004) for i in range(3)]
                gyro = [GYRO_BIAS[0] + random.gauss(0, 0.05), GYRO_BIAS[1] + random.gauss(0, 0.05),
                        GYRO_BIAS[2] + math.degrees(wz) + random.gauss(0, 0.05)]
                self.write(frame(FUNC_IMU, struct.pack('<6f', *acc, *gyro)))
            if now >= next_pad:
                next_pad += 1 / 20.0
                if now < self.pad_until:
                    b, h, ax = self.pad
                    self.write(frame(FUNC_GAMEPAD, struct.pack('<HB4b', b, h, *ax)))
                else:
                    self.write(frame(FUNC_GAMEPAD, bytes(7)))
            if now >= next_bat:
                next_bat += 1.0
                self.write(frame(FUNC_SYS, struct.pack('<BH', 0x04, self.battery_mv)))
            if self.key_pending:
                self.key_pending = False
                self.write(frame(FUNC_KEY, bytes([1, 0x01])))
            if now >= next_state:
                next_state += 0.05
                self.publish_state(vx, vy, wz)
            time.sleep(0.002)

    def publish_state(self, vx, vy, wz):
        with self.lock:
            s = {'t': time.time(), 'pty': self.slave_name, 'body_vel': [vx, vy, wz], 'pose': self.pose,
                 'wheel_rps': list(self.rps), 'servo': {str(k): round(v) for k, v in self.servo.items()},
                 'torque': {str(k): v for k, v in self.torque.items()}, 'buzzer': self.buzzer,
                 'counts': dict(self.counts)}
        if self.args.state:
            tmp = self.args.state + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(s, f)
            os.replace(tmp, self.args.state)
        if self.udp:
            self.udp[0].sendto(json.dumps({'vx': vx, 'vy': vy, 'wz': wz}).encode(), self.udp[1])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--link', default='/dev/ttyACM0')
    ap.add_argument('--state', default='/run/fake_board.json')
    ap.add_argument('--twist-udp', default='')
    ap.add_argument('--battery-mv', type=int, default=12450)
    a = ap.parse_args()
    b = Board(a)
    print(f'fake board on {b.slave_name} -> {a.link}', flush=True)
    b.run()


if __name__ == '__main__':
    main()
