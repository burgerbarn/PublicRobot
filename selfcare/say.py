"""What the robot wants to say out loud to its owner. A queue on the robot; Buddy (bigbuddy) polls it through the
robot API (GET /say, POST /say/ack) and speaks. The robot never connects out.
priority: urgent (always spoken) | normal (not during quiet hours 22:30-07:30)."""
import os, time

from common import DATA, load, save

PATH = os.path.join(DATA, 'say_queue.json')


def enqueue(key, text, priority='normal', repeat_s=7200, expires_s=1800):
    """Add a message unless the same key was queued or spoken within repeat_s. Returns True when added."""
    if os.environ.get('SELFCARE_DRY'):                      # measure-only round: nothing is queued
        return False
    q = load(PATH, []); now = time.time()
    q = [m for m in q if now - m['t'] < 86400]
    last = max((m.get('said_t') or m['t'] for m in q if m['key'] == key), default=0)
    if now - last < repeat_s:
        save(PATH, q); return False
    q.append(dict(id=f'{int(now * 1000)}', key=key, text=text, priority=priority, t=now, expires=now + expires_s, said_t=None))
    save(PATH, q); return True


def cancel(key):
    q = load(PATH, []); save(PATH, [m for m in q if not (m['key'] == key and m.get('said_t') is None)])


def pending():
    """The next message to speak, or None."""
    now = time.time(); lt = time.localtime(now); quiet = (lt.tm_hour, lt.tm_min) >= (22, 30) or (lt.tm_hour, lt.tm_min) < (7, 30)
    c = [m for m in load(PATH, []) if m.get('said_t') is None and m['expires'] > now and (m['priority'] == 'urgent' or not quiet)]
    c.sort(key=lambda m: (m['priority'] != 'urgent', m['t']))
    return c[0] if c else None


def ack(mid):
    q = load(PATH, [])
    for m in q:
        if m['id'] == mid:
            m['said_t'] = time.time()
    save(PATH, q)
