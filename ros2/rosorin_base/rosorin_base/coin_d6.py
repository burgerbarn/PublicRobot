"""COIN-D6 LiDAR serial protocol, as verified on this robot (docs/hardware.md, 2026-09-26).

Matches the YDLIDAR TOF 'intensity' packet layout (checksum verified 524/524):
  0-1  PH  0xAA 0x55
  2    CT  bit0 = ring-start packet
  3    LSN sample count
  4-5  FSA first angle, (u16 >> 1) / 64 deg
  6-7  LSA last angle
  8-9  CS  XOR of u16 words: PH, CT|LSN, FSA, LSA, each sample S0 and (S2<<8|S1)
  10.. samples, 3 bytes: S0 intensity, distance mm = (S2 << 6) | (S1 >> 2)
Low 2 bits of S1: meaning unverified, ignored.
"""


def _w(b, i):
    return b[i] | (b[i + 1] << 8)


def checksum_ok(pkt: bytes) -> bool:
    n = pkt[3]
    x = _w(pkt, 0) ^ _w(pkt, 2) ^ _w(pkt, 4) ^ _w(pkt, 6)
    for k in range(n):
        o = 10 + 3 * k
        x ^= pkt[o]
        x ^= _w(pkt, o + 1)
    return x == _w(pkt, 8)


def decode(pkt: bytes):
    """-> (ring_start, [(angle_deg, distance_mm, intensity), ...])"""
    n = pkt[3]
    fsa = (_w(pkt, 4) >> 1) / 64.0
    lsa = (_w(pkt, 6) >> 1) / 64.0
    span = (lsa - fsa) % 360.0
    pts = []
    for k in range(n):
        o = 10 + 3 * k
        s0, s1, s2 = pkt[o], pkt[o + 1], pkt[o + 2]
        a = (fsa + span * k / (n - 1)) % 360.0 if n > 1 else fsa
        pts.append((a, (s2 << 6) | (s1 >> 2), s0))
    return bool(pkt[2] & 1), pts


class PacketParser:
    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\xaa\x55')
            if i < 0:
                del self.buf[:-1]
                return out
            if i:
                del self.buf[:i]
            if len(self.buf) < 10:
                return out
            size = 10 + 3 * self.buf[3]
            if len(self.buf) < size:
                return out
            pkt = bytes(self.buf[:size])
            if checksum_ok(pkt):
                out.append(pkt)
                del self.buf[:size]
            else:
                self.bad += 1
                del self.buf[:2]
