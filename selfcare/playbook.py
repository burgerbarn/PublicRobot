"""Known patterns -> what the robot does about them. Every action is from a short allow-list, is logged with the
evidence it rests on, is verified by measuring again, and is rolled back when it did not help.
Never: wheels, goals, deleting data, anything outside its own services and its own learned settings.
Changes to services happen only while the robot is idle (wheels disabled, no wheel skill running)."""
import os, time

import checks, say
from common import DATA, DISABLED, Ledger, append, run, skill_running, unit_active

ALLOWED_UNITS = ('rosorin-base', 'rosorin-camera', 'rosorin-vslam', 'rosorin-contact', 'rosorin-api', 'rosorin-mind', 'rosorin-nav')
ACTIONS = os.path.join(DATA, 'actions.jsonl')
DRY = bool(os.environ.get('SELFCARE_DRY'))


def idle(L):
    d = (L.get('driver') or {}).get('facts', {})
    return d.get('enabled') is False and skill_running() is None


def allowed():
    return not os.path.exists(DISABLED) and not DRY


def log(action, evidence, **kw):
    rec = dict(t=round(time.time(), 1), action=action, evidence=evidence, **kw)
    append(ACTIONS, rec); print('ACTION', rec, flush=True)
    return rec


def sudo(*cmd, timeout=90):
    return run(['sudo', '-n', *cmd], timeout=timeout)[0] == 0


def restart_units(units):
    """Restart some of the robot's own services in a safe order. Navigation depends on the base and must not come
    back within seconds of being stopped (docs/research/always_on_navigation_reference.md section 6)."""
    units = [u for u in units if u in ALLOWED_UNITS]
    nav = 'rosorin-nav' in units or ('rosorin-base' in units and unit_active('rosorin-nav'))
    if nav:
        sudo('systemctl', 'stop', 'rosorin-nav'); t_stop = time.time()
    if 'rosorin-base' in units:
        sudo('systemctl', 'restart', 'rosorin-base'); time.sleep(8)
    rest = [u for u in units if u not in ('rosorin-base', 'rosorin-nav')]
    if rest:
        sudo('systemctl', 'restart', *rest); time.sleep(8)
    if nav:
        time.sleep(max(0.0, 20.0 - (time.time() - t_stop)))
        sudo('systemctl', 'start', 'rosorin-nav')
    return units


def decide(L, exp, base):
    """Go through the evidence; act where a rule applies. Returns the findings (each: what, evidence ids, action, result)."""
    F = []
    def finding(what, ev, action='none', result='open', **kw):
        f = dict(what=what, evidence=[e['id'] for e in ev if e], action=action, result=result, **kw); F.append(f); return f
    lim = exp['limits']
    E = L.get

    # 1. processes that do not share memory with the rest (2026-10-05: 65,000 packets/s, load 30)
    h = E('host_ids')
    if h and h['status'] == 'FAIL':
        units = h['facts']['stale_units']
        if not allowed():
            finding('some processes do not share memory with the rest', [h, E('network')], 'not allowed (disabled or dry run)', units=units)
        elif not idle(L):
            finding('some processes do not share memory with the rest', [h, E('network')], 'deferred: the robot is driving', units=units)
        else:
            log('restart_units', [h['id']], units=units, why='different middleware host id')
            restart_units(units); time.sleep(25)
            V = Ledger(); checks.ros_checks(V, exp, base); checks.network(V, exp)
            ok = V.get('host_ids')['status'] == 'PASS'
            finding('some processes did not share memory with the rest', [h, E('network')], f'restarted {", ".join(units)}',
                    'fixed' if ok else 'not fixed', after=dict(host_ids=V.get('host_ids')['text'], network=V.get('network')['text']))
            if not ok:
                say.enqueue('host_ids', 'Some of my programs are not talking to each other properly and restarting them did not fix it.')

    # 2. a process far above its usual CPU: look at its threads; known remedy for the OpenBLAS idle-thread fault
    c = E('cpu_by_process')
    high = dict((c or {}).get('facts', {}).get('high') or {})
    if high:                                                # measure again: only what is high twice counts
        V = Ledger(); c2 = checks.cpu_by_process(V, exp, base)
        high = {n: v for n, v in high.items() if n in c2['facts']['high']}
    for name, (now, usual) in high.items():
        t = checks.cpu_by_thread(L, name)
        f = t['facts']; unit = f.get('unit')
        if f.get('spinning') and f.get('openblas') and f.get('openblas_threads') != '1' and unit in ALLOWED_UNITS:
            if not allowed() or not idle(L):
                finding(f'{name} wastes CPU in idle maths-library threads', [c, t], 'deferred' if allowed() else 'not allowed', unit=unit); continue
            d = f'/etc/systemd/system/{unit}.service.d'; conf = f'{d}/90-selfcare-openblas.conf'
            log('openblas_one_thread', [c['id'], t['id']], unit=unit, file=conf)
            sudo('mkdir', '-p', d)
            run(['sudo', '-n', 'bash', '-c', f'printf "[Service]\\nEnvironment=OPENBLAS_NUM_THREADS=1\\n" > {conf}'])
            sudo('systemctl', 'daemon-reload'); restart_units([unit]); time.sleep(20)
            V = Ledger(); v = checks.cpu_by_process(V, exp, base)
            after = v['facts']['cpu'].get(name, 0)
            if after <= max(usual * 1.4, usual + 20):
                finding(f'{name} wasted CPU in idle maths-library threads', [c, t], f'set its maths library to one thread and restarted {unit}',
                        'fixed', before=now, after=round(after))
            else:
                sudo('rm', '-f', conf); sudo('systemctl', 'daemon-reload'); restart_units([unit])
                finding(f'{name} uses more CPU than usual', [c, t], 'tried one maths thread: no change, rolled back', 'not fixed', before=now, after=round(after))
        else:
            finding(f'{name} uses {now} % of a core (usual {usual} %)', [c, t])

    # 3. a service of its own is not running
    s = E('services')
    for u in (s or {}).get('facts', {}).get('down', []):
        if u in ALLOWED_UNITS and allowed() and (idle(L) or u not in ('rosorin-base',)):
            log('start_unit', [s['id']], unit=u)
            sudo('systemctl', 'reset-failed', u); sudo('systemctl', 'start', u); time.sleep(15)
            ok = unit_active(u)
            finding(f'{u} was not running', [s], f'started {u}', 'fixed' if ok else 'not fixed')
            if not ok:
                say.enqueue(f'unit_{u}', f'One of my programs, {u.replace("rosorin-", "")}, will not start.')
        else:
            finding(f'{u} is not running', [s], 'deferred or not allowed')

    # 4. a sensor topic is silent: restart the service that owns it (once per hour)
    r = E('topic_rates')
    owners = sorted({exp['owner'][t] for t in (r or {}).get('facts', {}).get('slow', {}) if t in exp['owner']})
    if owners:                                              # measure again before touching anything: one slow reading is not a fact
        V = Ledger(); checks.ros_checks(V, exp, base); r2 = V.get('topic_rates')
        owners = sorted({exp['owner'][t] for t in r2['facts'].get('slow', {}) if t in exp['owner']} & set(owners))
        if not owners:
            finding('topics looked slow once; a second measurement was normal', [r], 'none needed', 'not confirmed')
    for u in owners:
        if s and u in s['facts'].get('down', []):
            continue                                            # handled above
        if allowed() and idle(L) and say.enqueue(f'restarted_{u}', '', repeat_s=3600, expires_s=0):
            log('restart_units', [r['id']], units=[u], why='its topics are slow or silent')
            restart_units([u]); time.sleep(25)
            V = Ledger(); checks.ros_checks(V, exp, base)
            ok = V.get('topic_rates')['status'] == 'PASS'
            finding(f'topics of {u} were slow or silent', [r], f'restarted {u}', 'fixed' if ok else 'not fixed', after=V.get('topic_rates')['text'])
        else:
            finding(f'topics of {u} are slow or silent', [r], 'deferred, not allowed, or already tried this hour')

    # 5. battery: ask to be plugged in (owner's request 2026-10-05)
    b = E('battery')
    if b and b['facts'].get('volts') is not None:
        v, plugged = b['facts']['volts'], b['facts'].get('plugged')
        if plugged:
            say.cancel('battery')
        elif v <= lim['battery_urgent_v']:
            say.enqueue('battery', 'My battery is very low. Please plug me in now.', 'urgent', repeat_s=300, expires_s=600)
            finding('battery very low and not plugged in', [b], 'asked the owner (urgent)', 'waiting for the owner')
        elif v <= lim['battery_ask_v']:
            say.enqueue('battery', 'My battery is getting low. Please plug me in.', 'urgent', repeat_s=600, expires_s=900)
            finding('battery low and not plugged in', [b], 'asked the owner', 'waiting for the owner')

    # 6. lost on its map for a long time
    n = E('nav_status')
    if n and n['facts'].get('state') == 'lost':
        since = base.d.get('_lost_since') or time.time(); base.d['_lost_since'] = since
        if time.time() - since > lim['lost_min'] * 60:
            say.enqueue('lost', 'I do not know where I am on my map. If you moved me, please put me back at my home spot.', repeat_s=7200)
            finding(f'lost on the map for {(time.time() - since) / 60:.0f} minutes', [n], 'asked the owner', 'waiting for the owner')
        else:
            finding('lost on the map', [n], 'waiting: navigation keeps looking by itself')
    else:
        base.d.pop('_lost_since', None)

    # 7. network overload without a known cause, resources, repeated failed drives, navigation restarts
    w = E('network')
    if w and w['status'] == 'FAIL' and not (h and h['status'] == 'FAIL'):
        finding('heavy traffic between my own programs through the kernel', [w, h])
    q = E('resources')
    if q and q['status'] == 'FAIL':
        say.enqueue('resources', 'I am running short of something: ' + q['text'] + '.', repeat_s=21600)
        finding(q['text'], [q], 'told the owner')
    o = E('run_outcomes')
    if o and o['status'] == 'FAIL' and (o['facts'].get('newest_age_h') or 99) > 2:
        finding('the last three recorded drives failed (none in the last two hours)', [o], 'none: old news', 'noted')
    elif o and o['status'] == 'FAIL':
        say.enqueue('runs_' + o['facts']['runs'][-1]['run'], 'My last three drives did not go well. The last one ended with: ' + o['facts']['runs'][-1]['reason'] + '.', repeat_s=10 ** 9)
        finding('the last three drives failed', [o], 'told the owner')
    nr = (s or {}).get('facts', {}).get('nav_restarts')
    prev = base.d.get('_nav_restarts', nr)
    if nr is not None and prev is not None and nr - prev >= 3:
        finding(f'navigation restarted {nr - prev} times since the last check', [s])
    if nr is not None:
        base.d['_nav_restarts'] = nr
    return F
