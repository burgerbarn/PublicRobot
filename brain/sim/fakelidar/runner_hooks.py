"""run_chapter.py --fake-lidar: start/stop the fake LiDAR in the container and report what it did.

Kept apart from run_chapter.py so the shared runner only calls start/stop/report/write. The LiDAR itself is
fake_lidar.py (in the image at /opt/fakelidar/)."""
import json
import os
import subprocess
import time

CMD = 'python3 /opt/fakelidar/fake_lidar.py'


def start(box):
    """Start a fresh fake LiDAR as root (/dev/ttyUSB0 -> pty, /dev/lidar -> ttyUSB0). -> True when /dev/lidar is up."""
    stop(box)
    box.root('rm -rf /tmp/fakelidar && mkdir -p /tmp/fakelidar')
    subprocess.run(['docker', 'exec', '-d', '-u', 'root', box.name, 'bash', '-c',
                    f'exec {CMD} > /tmp/fakelidar/lidar.out 2>&1'], capture_output=True)
    for _ in range(50):
        rc, _, _ = box.root('test -c /dev/lidar')
        if rc == 0:
            return True
        time.sleep(0.1)
    return False


def stop(box):
    box.root(f"pkill -TERM -f '^{CMD}' ; sleep 0.5; pkill -KILL -f '^{CMD}'; true")


def report(box):
    """status.json counters + the open/close events (from /tmp/fakelidar)."""
    rc, out, _ = box.root('cat /tmp/fakelidar/status.json 2>/dev/null')
    try:
        st = json.loads(out)
    except ValueError:
        return {'error': 'no status.json', 'lidar_out': box.root('cat /tmp/fakelidar/lidar.out')[1][-2000:]}
    rc, ev, _ = box.root('cat /tmp/fakelidar/events.jsonl 2>/dev/null')
    st['events'] = [json.loads(l) for l in ev.splitlines() if l.strip()][-60:]
    return st


def write(results, ch, st):
    """chNN.fakelidar.json + a "Fake LiDAR" section appended to chNN.md."""
    os.makedirs(results, exist_ok=True)
    json.dump(st, open(os.path.join(results, f'{ch}.fakelidar.json'), 'w'), indent=1)
    if st.get('error'):
        md = f'\n## Fake LiDAR\n\n{st["error"]}\n```text\n{st.get("lidar_out", "")}\n```\n'
    else:
        md = '\n'.join(['', '## Fake LiDAR', '',
                        f'- {st.get("link")} -> {os.path.basename(st.get("tty_link") or "")} -> {st.get("pts")}; '
                        f'opened {st.get("opens")} times, closed {st.get("closes")}',
                        f'- {st.get("hz")} revolutions/s; {st.get("revolutions")} revolutions turned, '
                        f'{st.get("revolutions_sent")} (partly) sent to a reader; {st.get("tx_bytes")} bytes sent, '
                        f'{st.get("tx_dropped_bytes")} dropped, {st.get("rx_bytes_ignored")} bytes written to it '
                        f'(ignored)',
                        f'- scene: {st.get("scene")}', ''])
    with open(os.path.join(results, f'{ch}.md'), 'a') as f:
        f.write(md)
