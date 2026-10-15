from rosorin_base.rrc_protocol import FrameParser, crc8_maxim, decode_battery_mv


def frame(func, payload):
    body = bytes([func, len(payload)]) + payload
    return b'\xaa\x55' + body + bytes([crc8_maxim(body)])


def test_battery_frame_from_robot():
    # real frame captured 2026-09-26: func 0, payload 04 7e 31
    p = FrameParser()
    out = p.feed(frame(0, bytes.fromhex('047e31')))
    assert out == [(0, bytes.fromhex('047e31'))]
    assert decode_battery_mv(out[0][1]) == 12670


def test_bad_crc_rejected_and_resync():
    good = frame(8, bytes(7))
    bad = bytearray(good); bad[-1] ^= 0xFF
    p = FrameParser()
    out = p.feed(bytes(bad) + b'\x00\x13' + good)
    assert out == [(8, bytes(7))] and p.crc_errors == 1


def test_split_across_reads():
    f = frame(7, bytes(24))
    p = FrameParser()
    assert p.feed(f[:10]) == []
    assert p.feed(f[10:]) == [(7, bytes(24))]
