"""Controller-board (STM32) serial protocol, as verified on this robot (docs/hardware.md).

Frame: 0xAA 0x55 | function | length | payload[length] | crc
crc = CRC-8/MAXIM (poly 0x31 reflected -> 0x8C, init 0) over function, length, payload.
"""
import struct

FUNC_SYS = 0
FUNC_IMU = 7
FUNC_GAMEPAD = 8
SYS_BATTERY = 0x04


def crc8_maxim(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8C if c & 1 else c >> 1
    return c


def encode_frame(func: int, payload: bytes) -> bytes:
    body = bytes([func, len(payload)]) + bytes(payload)
    return b'\xaa\x55' + body + bytes([crc8_maxim(body)])


class FrameParser:
    """Incremental parser: feed() bytes, get back (function, payload) for CRC-valid frames."""

    def __init__(self):
        self.buf = bytearray()
        self.crc_errors = 0

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
            if len(self.buf) < 5:
                return out
            n = self.buf[3]
            if len(self.buf) < 5 + n:
                return out
            body = bytes(self.buf[2:4 + n])
            crc = self.buf[4 + n]
            if crc == crc8_maxim(body):
                out.append((body[0], body[2:]))
                del self.buf[:5 + n]
            else:
                self.crc_errors += 1
                del self.buf[:2]


def decode_imu(payload: bytes):
    """-> (ax, ay, az) in g, (gx, gy, gz) in deg/s."""
    ax, ay, az, gx, gy, gz = struct.unpack('<6f', payload)
    return (ax, ay, az), (gx, gy, gz)


def decode_battery_mv(payload: bytes):
    """-> battery millivolts, or None if not a battery report."""
    if len(payload) == 3 and payload[0] == SYS_BATTERY:
        return struct.unpack('<H', payload[1:])[0]
    return None
