"""One self-care round (rosorin-selfcheck.service, every 15 min and after every wheel skill): measure, compare with
what is normal for this robot, act where a rule allows it, verify, ask the brain about what is left, tell the owner
what is his. Writes ~/selfcare/data/reports/<day>.jsonl and latest.json.
Usage: self_check.py [--dry] (measure and report only)   Env SELFCARE_TEST_BATTERY="10.8,0" fakes a battery for tests."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if '--dry' in sys.argv:
    os.environ['SELFCARE_DRY'] = '1'
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

import checks, playbook
from baseline import Baseline
from common import DATA, DISABLED, HERE, Ledger, append, load, save, skill_running
from investigate import investigate


def main():
    t0 = time.time(); exp = load(os.path.join(HERE, 'expected.json'), {}); base = Baseline(); L = Ledger()
    driving = skill_running()
    checks.services(L, exp)
    try:
        checks.ros_checks(L, exp, base)
    except Exception as e:
        L.add('ros', 'FAIL', f'could not look at the ROS graph: {type(e).__name__}: {str(e)[:100]}')
    checks.cpu_by_process(L, exp, base)
    checks.network(L, exp)
    checks.resources(L, exp)
    checks.battery(L, exp)
    checks.run_outcomes(L)
    findings = playbook.decide(L, exp, base)
    for f in findings:                                   # what no rule explained goes to the brain (it only looks)
        if f['action'] == 'none' and f['result'] == 'open':
            f['brain'] = investigate(L, f, exp)
    healthy = all(f['result'] in ('noted', 'not confirmed') for f in findings) and \
        all(e['status'] == 'PASS' for e in L.items if e['check'] != 'run_outcomes')
    if healthy and not driving:                          # normal is learned only from healthy, standing moments
        vals = {f'cpu.{n}': v for n, v in L.get('cpu_by_process')['facts']['cpu'].items()}
        vals.update({f'rate.{t}': r for t, r in (L.get('topic_rates') or {'facts': {'rates': {}}})['facts']['rates'].items()})
        base.learn(vals)
    else:
        save(os.path.join(DATA, 'baseline.json'), base.d)
    rep = dict(t=round(t0, 1), time=time.strftime('%Y-%m-%d %H:%M:%S'), took_s=round(time.time() - t0, 1), healthy=healthy,
               driving=driving, disabled=os.path.exists(DISABLED), evidence=L.items, findings=findings)
    os.makedirs(os.path.join(DATA, 'reports'), exist_ok=True)
    append(os.path.join(DATA, 'reports', time.strftime('%Y%m%d') + '.jsonl'), rep)
    save(os.path.join(DATA, 'latest.json'), rep)
    print(f"self-check {rep['time']} ({rep['took_s']} s): " + ('healthy' if healthy else f'{len(findings)} finding(s)'))
    for e in L.items:
        print(f"  {e['id']:>3s} {e['status']:4s} {e['check']}: {e['text']}"[:230])
    for f in findings:
        print(f"  -> {f['what']} | {f['action']} | {f['result']}" + (f" | brain: {f['brain']}" if f.get('brain') else ''))


if __name__ == '__main__':
    main()
