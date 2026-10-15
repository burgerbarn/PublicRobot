"""Self-care: shared pieces - where things are kept, the evidence ledger, small helpers.
The robot notices, diagnoses, fixes what it is allowed to fix, verifies, and asks its owner for the rest
(docs/research/doing_it_itself_reference.md). Code decides what is true and what is allowed; the brain model only
chooses which further check to run and names a cause."""
import json, os, subprocess, time

HOME = os.path.expanduser('~')
DATA = os.path.join(HOME, 'selfcare', 'data')
HERE = os.path.dirname(os.path.abspath(__file__))
DISABLED = os.path.join(HOME, 'selfcare', 'DISABLED')            # owner's switch: touch this file and nothing is changed
SKILL_UNITS = ('rosorin-explore', 'rosorin-practice', 'rosorin-task@come', 'rosorin-task@home', 'rosorin-task@sethome')
os.makedirs(DATA, exist_ok=True)


def load(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def save(path, obj):
    tmp = path + '.tmp'
    json.dump(obj, open(tmp, 'w'), indent=1)
    os.replace(tmp, path)


def append(path, obj):
    with open(path, 'a') as f:
        f.write(json.dumps(obj) + '\n')


def run(cmd, timeout=30):
    """(return code, stdout) of a command; never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def unit_active(unit):
    return run(['systemctl', 'is-active', '--quiet', unit])[0] == 0


def skill_running():
    return next((u for u in SKILL_UNITS if unit_active(u)), None)


class Ledger:
    """Numbered evidence. Every finding, action and diagnosis refers to these ids."""

    def __init__(self):
        self.items = []

    def add(self, check, status, text, facts=None, args=None):
        e = dict(id=f'E{len(self.items) + 1}', check=check, args=args or {}, status=status, text=text,
                 facts=facts or {}, t=round(time.time(), 1))
        self.items.append(e)
        return e

    def get(self, check):
        return next((e for e in reversed(self.items) if e['check'] == check), None)

    def brief(self):
        return ' '.join(f"{e['id']} {e['check']}: {e['text']}" for e in self.items)
