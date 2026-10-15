"""Motion test 1: does the board stop the motors by itself when commands stop?
One slow command, 5 s silence, then explicit zero. Robot must be on a stand."""
import os, struct, sys, termios, time
sys.path.insert(0, "/home/burgerbarn/ros2_ws/src/rosorin_base")
from rosorin_base.rrc_protocol import FrameParser, crc8_maxim

def frame(func, payload):
    body = bytes([func, len(payload)]) + payload
    return b"\xaa\x55" + body + bytes([crc8_maxim(body)])

def motors(rps):
    p = bytes([0x01, 4]) + b"".join(struct.pack("<Bf", i, r) for i, r in enumerate(rps))
    return frame(3, p)

go, stop = motors([0.3, 0.3, 0.3, 0.3]), motors([0.0, 0.0, 0.0, 0.0])
for f in (go, stop):                                    # self-check with our own parser
    assert FrameParser().feed(f) == [(3, f[4:-1])], "frame self-check failed"
print("go  :", go.hex(" ")); print("stop:", stop.hex(" "))
if "--dry" in sys.argv: sys.exit(0)
fd = os.open("/dev/ttyACM0", os.O_RDWR | os.O_NOCTTY)
a = termios.tcgetattr(fd); a[0]=a[1]=a[3]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[4]=a[5]=termios.B1000000
termios.tcsetattr(fd, termios.TCSANOW, a)
try:
    for i in (3, 2, 1): print(f"start in {i}", flush=True); time.sleep(1)
    t0 = time.monotonic(); os.write(fd, go); print("t=0.0 GO sent (single command)", flush=True)
    while time.monotonic() - t0 < 5.0: time.sleep(0.5); print(f"t={time.monotonic()-t0:.1f} silent", flush=True)
finally:
    os.write(fd, stop); os.write(fd, stop); print("STOP sent", flush=True); os.close(fd)
