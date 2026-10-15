"""Write the stop frame directly to the board (systemd ExecStopPost; also usable by hand)."""
import os
import sys
import termios
import time

from rosorin_base.motion import STOP_FRAME


WAIT_S = 30.0   # board USB drop (tested 2026-09-28: gone ~3 s, re-enumerates as ttyACM0); board has no
                # stop watchdog, so keep trying until it is back. After this, systemd restarts the
                # service (RestartSec=3) and this runs again; board_driver also sends STOP at start.


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else WAIT_S
    t0, warned = time.monotonic(), False
    while True:                                 # port may be closing, or the device re-enumerating
        try:
            fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
            break
        except OSError as e:
            if time.monotonic() - t0 > wait:
                print(f'stop_motors: cannot open {dev} for {wait:.0f} s: {e}', file=sys.stderr)
                sys.exit(1)
            if not warned and time.monotonic() - t0 > 1.0:
                print(f'stop_motors: {dev} unavailable ({e}); retrying up to {wait:.0f} s', file=sys.stderr)
                warned = True
            time.sleep(0.2)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B1000000
    termios.tcsetattr(fd, termios.TCSANOW, a)
    for _ in range(3):
        os.write(fd, STOP_FRAME)
    os.close(fd)
    print(f'stop_motors: stop frame sent x3 (after {time.monotonic() - t0:.1f} s)')
