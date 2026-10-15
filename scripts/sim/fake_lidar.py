#!/usr/bin/env python3
"""Emulated COIN-D6 LiDAR for the simulated rebuild: Gazebo's scans as the real sensor's serial stream on a pty
(/dev/lidar), so the guide's own lidar_reader.py / coin_d6.py decode it.

Facts used (docs/hardware.md, verified 2026-09-26/28): YDLIDAR TOF "intensity" packets, streams with no start
command, ~10 rings/s, ~401 samples/ring in 17 packets (a 1-sample ring-start packet + 16 x 25), max ~7.94 m,
native angles run CLOCKWISE with robot forward = native 180 deg (left = native 90 deg), self-returns from the robot's
body at native 33-57 deg (51-88 mm) and 291-316 deg (50-106 mm).
Input: UDP JSON {"angle_min", "angle_increment", "ranges"} in the robot frame (CCW, 0 = forward) from gz_relay.py.
Without input (Gazebo not running) it streams nothing, like an unpowered sensor.
"""
import argparse, fcntl, grp, json, math, os, pty, select, socket, struct, termios, threading, time


def word(b, i):
    return b[i] | (b[i + 1] << 8)


def packet(ring_start, angles, dists, intens):
    n = len(angles)
    fsa = (int(round(angles[0] * 64)) << 1) | 1
    lsa = (int(round(angles[-1] * 64)) << 1) | 1
    body = bytearray()
    for mm, it in zip(dists, intens):
        mm = max(0, min(int(mm), 0x3FFF))
        body += bytes([it & 0xFF, (mm & 0x3F) << 2, mm >> 6])
    # ring start CT = 0xC9 as the real scanner sends it (bit 0 set; chapter 14's sample)
    head = bytearray(b'\xaa\x55') + bytes([0xC9 if ring_start else 0, n]) + struct.pack('<HH', fsa & 0xFFFF, lsa & 0xFFFF)
    x = word(head, 0) ^ word(head, 2) ^ word(head, 4) ^ word(head, 6)
    for k in range(n):
        o = 3 * k
        x ^= body[o]
        x ^= body[o + 1] | (body[o + 2] << 8)
    return bytes(head + struct.pack('<H', x) + body)


def ring_packets(scan):
    """Robot-frame scan -> list of COIN-D6 packets for one ring, native angles ascending from 0."""
    a0, da, rs = scan['angle_min'], scan['angle_increment'], scan['ranges']
    samples = []
    for i, r in enumerate(rs):
        a_robot = math.degrees(a0 + i * da)
        native = (180.0 - a_robot) % 360.0
        if 33 <= native <= 57:
            mm, it = 70, 40                     # the robot's own body, as measured
        elif 291 <= native <= 316:
            mm, it = 80, 40
        elif r is None or not math.isfinite(r) or r >= 7.94 or r <= 0.05:
            mm, it = 0, 0                       # no return
        else:
            mm, it = int(r * 1000), 120
        samples.append((native, mm, it))
    samples.sort()
    out = [packet(True, [samples[0][0]], [samples[0][1]], [samples[0][2]])]
    rest = samples[1:]
    for k in range(0, len(rest), 25):
        chunk = rest[k:k + 25]
        out.append(packet(False, [c[0] for c in chunk], [c[1] for c in chunk], [c[2] for c in chunk]))
    return out


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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--link', default='/dev/lidar')
    ap.add_argument('--listen', default='0.0.0.0:5601')
    a = ap.parse_args()
    master, slave_fd = pty.openpty()             # slave stays open: the port never hangs up
    name = os.ttyname(slave_fd)
    os.set_blocking(master, False)
    try:
        os.unlink(a.link)
    except FileNotFoundError:
        pass
    os.symlink(name, a.link)
    try:
        os.chown(name, -1, grp.getgrnam('dialout').gr_gid)
        os.chmod(name, 0o660)
    except (KeyError, PermissionError):
        pass
    h, p = a.listen.rsplit(':', 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((h, int(p)))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    port = PortUsers(slave_fd, name)
    print(f'fake lidar on {name} -> {a.link}, scans from udp {a.listen}', flush=True)
    rings = 0
    while True:
        r, _, _ = select.select([sock], [], [], 1.0)
        if not r:
            continue
        data = sock.recv(1 << 20)
        while True:                              # keep only the newest scan, like a sensor that is not waiting
            r, _, _ = select.select([sock], [], [], 0)
            if not r:
                break
            data = sock.recv(1 << 20)
        try:
            pkts = ring_packets(json.loads(data))
        except (ValueError, KeyError, IndexError):
            continue
        t0 = time.monotonic()
        for i, pk in enumerate(pkts):            # spread one ring over ~90 ms, as the spinning head does
            if port.count() and port.room(len(pk)):    # closed port or reader behind: bytes are lost, as on the real port
                try:
                    os.write(master, pk)
                except OSError:
                    pass
            time.sleep(max(0.0, t0 + 0.09 * (i + 1) / len(pkts) - time.monotonic()))
        rings += 1


if __name__ == '__main__':
    main()
