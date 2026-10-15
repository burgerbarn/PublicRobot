import os
from rosorin_base.coin_d6 import PacketParser, checksum_ok, decode

SAMPLE = os.path.join(os.path.dirname(__file__), 'coin_d6_sample.bin')   # real capture 2026-09-26


def test_real_capture_all_checksums_ok():
    p = PacketParser()
    pkts = p.feed(open(SAMPLE, 'rb').read())
    # The capture starts mid-packet and 0xAA 0x55 can occur inside payload bytes, so the parser may
    # hit a false header; it must reject it by checksum and resync, never return a bad packet.
    assert len(pkts) >= 30 and p.bad <= 2
    assert all(checksum_ok(x) for x in pkts)


def test_decode_shape():
    pkts = PacketParser().feed(open(SAMPLE, 'rb').read())
    normal = [decode(x) for x in pkts if x[3] == 25]
    assert normal and all(len(pts) == 25 for _, pts in normal)
    for _, pts in normal:
        assert all(0 <= a < 360 and 0 <= mm <= 16383 for a, mm, _ in pts)


def test_corrupted_packet_rejected():
    pkts = PacketParser().feed(open(SAMPLE, 'rb').read())
    bad = bytearray(pkts[1]); bad[12] ^= 0x40
    p = PacketParser()
    assert p.feed(bytes(bad)) == [] and p.bad == 1
