"""The checks: read-only measurements, each returns one piece of evidence (status PASS / WARN / FAIL, one line of
text, facts). Cheap enough to run every few minutes next to a driving robot; none of them changes anything."""
import glob, json, os, re, time

from common import HOME, load, run, unit_active, skill_running

CLK = os.sysconf('SC_CLK_TCK')


def _cmd(pid):
    try:
        return open(f'/proc/{pid}/cmdline').read().replace('\0', ' ').strip()
    except OSError:
        return ''


def unit_of(pid):
    try:
        m = re.search(r'/([^/]+\.service)', open(f'/proc/{pid}/cgroup').read())
        return m.group(1)[:-8] if m else None
    except OSError:
        return None


def name_of(pid):
    c = _cmd(pid)
    m = re.search(r'__node:=(\S+)', c)
    if m:
        return m.group(1)
    a = c.split()
    if not a:
        return None
    for x in a[:3]:
        if x.endswith('.py'):
            return os.path.basename(x)[:-3]
    b = os.path.basename(a[0])
    if b.startswith('python') and len(a) > 1 and not a[1].startswith('-'):
        b = os.path.basename(a[1])                      # an installed Python node: /usr/bin/python3 .../lib/pkg/<node>
    return 'aurora' if b.startswith('aurora930') else b


def robot_pids():
    """pid -> (name, unit) of every process that belongs to one of the robot's own services."""
    out = {}
    for p in os.listdir('/proc'):
        if p.isdigit():
            u = unit_of(p)
            if u and u.startswith('rosorin-'):
                n = name_of(p)
                if n and n not in ('bash', 'ros2', 'sudo', 'python3', 'python'):
                    out[int(p)] = (n, u)
    return out


def _jiffies(pid):
    f = open(f'/proc/{pid}/stat').read().rsplit(') ', 1)[1].split()
    return int(f[11]), int(f[12])


def cpu_by_process(L, exp, base, secs=4.0):
    pids = robot_pids(); a = {}
    for p in pids:
        try: a[p] = sum(_jiffies(p))
        except OSError: pass
    time.sleep(secs)
    cpu = {}
    for p, j in a.items():
        try: cpu[pids[p][0]] = cpu.get(pids[p][0], 0.0) + (sum(_jiffies(p)) - j) / CLK / secs * 100
        except OSError: pass
    load1 = float(open('/proc/loadavg').read().split()[0])
    high = {}
    for n, v in cpu.items():
        usual = base.usual(f'cpu.{n}', exp['cpu'].get(n, exp['cpu_default']))
        if v > max(usual * 1.6, usual + 30):
            high[n] = (round(v), round(usual))
    text = (', '.join(f'{n} {v} % of a core (usual {u} %)' for n, (v, u) in high.items()) or 'every process near its usual CPU') + \
        f'; load average {load1:.1f}, all robot processes {sum(cpu.values()):.0f} %'
    units = {n: u for (n, u) in pids.values()}
    return L.add('cpu_by_process', 'FAIL' if high else ('WARN' if load1 > exp['limits']['load1'] else 'PASS'), text,
                 dict(cpu={n: round(v, 1) for n, v in cpu.items()}, high=high, load1=load1, units=units))


def cpu_by_thread(L, process, secs=3.0):
    """Which threads of one process use the CPU, and are they busy-waiting? Signature calibrated 2026-10-05 on the
    OpenBLAS idle-thread fault: each spinning thread ~76 % of a core, 80 % of it in the kernel (sched_yield)."""
    pid = next((p for p, (n, _) in robot_pids().items() if n == process), None)
    if pid is None:
        return L.add('cpu_by_thread', 'WARN', f'no process named {process}', args=dict(process=process))
    def snap():
        d = {}
        for t in os.listdir(f'/proc/{pid}/task'):
            try:
                f = open(f'/proc/{pid}/task/{t}/stat').read().rsplit(') ', 1)[1].split(); d[t] = (int(f[11]), int(f[12]))
            except OSError: pass
        return d
    a = snap(); time.sleep(secs); b = snap(); hot = []
    for t in b:
        if t in a:
            u, k = [(b[t][i] - a[t][i]) / CLK / secs * 100 for i in (0, 1)]
            if u + k >= 15:
                hot.append(dict(tid=int(t), cpu=round(u + k), kernel_share=round(k / (u + k), 2)))
    spin = [h for h in hot if h['cpu'] >= 40 and h['kernel_share'] >= 0.6]
    try:
        blas = 'libopenblas' in open(f'/proc/{pid}/maps').read()
        env = dict(x.split('=', 1) for x in open(f'/proc/{pid}/environ').read().split('\0') if '=' in x)
    except OSError:
        blas, env = False, {}
    text = f'{process}: {len(hot)} busy threads ' + ', '.join(f"{h['cpu']} % ({int(h['kernel_share'] * 100)} % in the kernel)" for h in hot)
    if spin:
        text += f'; {len(spin)} of them are busy-waiting' + (' and the process uses the OpenBLAS maths library with its thread pool on' if blas and env.get('OPENBLAS_NUM_THREADS') != '1' else '')
    return L.add('cpu_by_thread', 'FAIL' if spin else 'PASS', text, args=dict(process=process),
                 facts=dict(pid=pid, unit=unit_of(pid), hot=hot, spinning=len(spin), openblas=blas,
                            openblas_threads=env.get('OPENBLAS_NUM_THREADS')))


def _udp():
    u = [x for x in open('/proc/net/snmp') if x.startswith('Udp:')][1].split()
    return int(u[1]), int(u[5])


def network(L, exp, secs=3.0):
    """Traffic between the robot's own processes that goes through the kernel instead of shared memory."""
    a = _udp(); time.sleep(secs); b = _udp()
    pk, er = (b[0] - a[0]) / secs, (b[1] - a[1]) / secs
    lim = exp['limits']
    bad = pk > lim['udp_packets_per_s'] or er > lim['rcvbuf_errors_per_s']
    return L.add('network', 'FAIL' if bad else 'PASS',
                 f'{pk:.0f} UDP packets/s received, {er:.0f} kernel receive-buffer overflows/s (normal: a few hundred, 0)',
                 dict(udp_packets_per_s=round(pk), rcvbuf_errors_per_s=round(er, 1)))


def resources(L, exp):
    lim = exp['limits']; st = os.statvfs(HOME); free_gb = st.f_bavail * st.f_frsize / 1e9
    mem = dict(l.split(':') for l in open('/proc/meminfo')); avail = int(mem['MemAvailable'].split()[0]) / 1024
    temps = {}
    for z in glob.glob('/sys/class/thermal/thermal_zone*'):
        try: temps[open(z + '/type').read().strip()] = int(open(z + '/temp').read()) / 1000
        except (OSError, ValueError): pass
    hot = max(temps.values()) if temps else 0
    bad = [w for w, c in ((f'disk {free_gb:.0f} GB free', free_gb < lim['disk_free_gb']),
                          (f'memory {avail:.0f} MB available', avail < lim['ram_avail_mb']),
                          (f'hottest sensor {hot:.0f} C', hot > lim['temp_c'])) if c]
    return L.add('resources', 'FAIL' if bad else 'PASS', '; '.join(bad) or
                 f'disk {free_gb:.0f} GB free, memory {avail:.0f} MB available, hottest sensor {hot:.0f} C',
                 dict(disk_free_gb=round(free_gb, 1), ram_avail_mb=round(avail), temp_c=round(hot, 1)))


def services(L, exp):
    down = [u for u in exp['units'] if not unit_active(u)]
    failed = [l.split()[0] for l in run(['systemctl', 'list-units', 'rosorin*', '--state=failed', '--no-legend', '--plain'])[1].split('\n') if l.strip()]
    restarts = run(['systemctl', 'show', 'rosorin-nav', '-p', 'NRestarts', '--value'])[1]
    return L.add('services', 'FAIL' if down else 'PASS',
                 ('not running: ' + ', '.join(down)) if down else 'all the robot\'s services are running',
                 dict(down=down, failed=failed, nav_restarts=int(restarts) if restarts.isdigit() else None, skill=skill_running()))


def journal_errors(L, service):
    rc, out = run(['journalctl', '-u', service, '-b', '--no-pager', '-o', 'cat', '-p', 'warning', '-n', '400'])
    lines = [re.sub(r'\x1b\[[0-9;]*m|\[\d{10}\.\d+\]', '', l).strip() for l in out.split('\n') if re.search(r'error|fail|fatal|critical|Traceback', l, re.I)]
    seen = {}
    for l in lines:
        seen[l[:140]] = seen.get(l[:140], 0) + 1
    top = sorted(seen.items(), key=lambda x: -x[1])[:3]
    return L.add('journal_errors', 'WARN' if top else 'PASS',
                 f'{service}: ' + ('; '.join(f'{n}x "{t}"' for t, n in top) if top else 'no error lines since boot'),
                 args=dict(service=service), facts=dict(count=len(lines), top=top))


def battery(L, exp):
    lim = exp['limits']; f = os.path.join(HOME, 'battery', time.strftime('%Y%m%d', time.gmtime()) + '.csv')
    rows = []
    try:
        for l in open(f).read().strip().split('\n')[-240:]:
            a = l.split(',')
            if a[0].isdigit(): rows.append((int(a[0]), int(a[1]) / 1000.0))
    except (OSError, IndexError, ValueError):
        pass
    fake = os.environ.get('SELFCARE_TEST_BATTERY')                    # test only: "volts,plugged(0|1)"
    plugged = load(os.path.join(HOME, 'battery', 'plugged.json'), {}).get('plugged')
    if fake:
        rows = [(int(time.time()) - 600, float(fake.split(',')[0]) + 0.2), (int(time.time()), float(fake.split(',')[0]))]
        plugged = fake.split(',')[1] == '1'
    if not rows or time.time() - rows[-1][0] > 120:
        return L.add('battery', 'WARN', 'no fresh battery reading', dict(plugged=plugged))
    recent = [v for t, v in rows if t >= rows[-1][0] - 60]; v = sorted(recent)[len(recent) // 2]
    old = [v0 for t, v0 in rows if t <= rows[-1][0] - 540]
    slope = (v - old[-1]) / 9.0 if old else 0.0                        # V per minute over the last ~10 min
    left = (v - 10.3) / -slope if slope < -0.002 else None
    st = 'PASS' if plugged or v > lim['battery_ask_v'] else 'FAIL'
    text = f'{v:.2f} V, ' + ('plugged in' if plugged else 'not plugged in') + \
        (f', falling {-slope * 60:.2f} V per hour, about {left:.0f} min to the 10.3 V limit' if left and not plugged else '')
    return L.add('battery', st, text, dict(volts=round(v, 2), plugged=bool(plugged), v_per_min=round(slope, 4),
                                           minutes_left=None if left is None else round(left), test=bool(fake)))


def run_outcomes(L, n=5):
    runs = []
    for d in sorted(glob.glob(os.path.join(HOME, 'explore', '*')))[-n:]:
        try:
            s = [json.loads(l) for l in open(os.path.join(d, 'log.jsonl')) if '"ev": "summary"' in l]
        except (OSError, ValueError):
            s = []
        if s:
            g = s[-1].get('goals') or {}
            runs.append(dict(run=os.path.basename(d), reason=str(s[-1].get('reason'))[:60], driven_m=s[-1].get('driven_m'),
                             ok=g.get('SUCCEEDED', 0), failed=sum(v for k, v in g.items() if k != 'SUCCEEDED'),
                             estops=len(s[-1].get('estops') or [])))
    bad = [r for r in runs[-3:] if r['failed'] > r['ok'] or r['estops']]
    text = 'no recorded drives' if not runs else \
        f"last {len(runs)} drives: " + '; '.join(f"{r['driven_m']} m, {r['ok']} goals reached, {r['failed']} failed ({r['reason']})" for r in runs[-3:])
    newest = max((os.path.getmtime(os.path.join(HOME, 'explore', r['run'], 'log.jsonl')) for r in runs), default=0)
    return L.add('run_outcomes', 'FAIL' if len(bad) >= 3 else 'PASS', text,
                 dict(runs=runs, newest_age_h=round((time.time() - newest) / 3600, 1) if runs else None))


def ros_checks(L, exp, base, secs=4.0):
    """Everything that needs the ROS graph, with one short-lived node: one middleware host id for all processes,
    topic rates, navigation status, driver state."""
    import rclpy
    from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
    from rosidl_runtime_py.utilities import get_message
    from std_msgs.msg import String
    rclpy.init(); n = rclpy.create_node('selfcare_check'); got = {'c': {}}
    try:
        latched = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        n.create_subscription(String, '/nav/status', lambda m: got.__setitem__('nav', json.loads(m.data)), latched)
        n.create_subscription(String, '/rosorin_board_driver/state', lambda m: got.__setitem__('drv', json.loads(m.data)), 5)
        end = time.time() + 1.5
        while time.time() < end: rclpy.spin_once(n, timeout_sec=0.1)
        types = dict(n.get_topic_names_and_types())
        for t in exp['rates']:
            if t in types:
                got['c'][t] = 0
                n.create_subscription(get_message(types[t][0]), t, (lambda m, t=t: got['c'].__setitem__(t, got['c'][t] + 1)),
                                      qos_profile_sensor_data, raw=True)
        t0 = time.time()
        while time.time() - t0 < secs: rclpy.spin_once(n, timeout_sec=0.05)
        dt = time.time() - t0
        # host ids: bytes 2-3 of every endpoint GID (Fast DDS: MD5 of the IPv4 addresses when the process started),
        # bytes 4-5 = low 16 bits of the process id
        mine = None; hosts = {}
        for t in types:
            for info in list(n.get_publishers_info_by_topic(t)) + list(n.get_subscriptions_info_by_topic(t)):
                g = bytes(info.endpoint_gid); h = g[2:4].hex()
                if info.node_name == 'selfcare_check':
                    mine = h
                hosts.setdefault(h, set()).add(g[4] | g[5] << 8)
    finally:
        n.destroy_node(); rclpy.shutdown()
    pids = robot_pids()
    stale = {}
    for h, lows in hosts.items():
        if mine and h != mine:
            for p, (name, unit) in pids.items():
                if p & 0xFFFF in lows:
                    stale.setdefault(unit, set()).add(name)
    L.add('host_ids', 'FAIL' if stale else 'PASS',
          ('processes that do not share memory with the rest (started under other network addresses): ' +
           '; '.join(f"{u}: {', '.join(sorted(v))}" for u, v in stale.items())) if stale else
          f'all processes carry the same middleware host id ({mine})',
          dict(mine=mine, ids=sorted(hosts), stale_units=sorted(stale)))
    rates = {t: round(c / dt, 1) for t, c in got['c'].items()}; slow = {}
    for t, want in exp['rates'].items():
        usual = base.usual(f'rate.{t}', want); r = rates.get(t, 0.0)
        if r < 0.5 * usual:
            slow[t] = (r, round(usual, 1))
    L.add('topic_rates', 'FAIL' if slow else 'PASS',
          ('slow or silent: ' + ', '.join(f'{t} {r} Hz (usual {u})' for t, (r, u) in slow.items())) if slow else
          'all sensor and odometry topics at their usual rates', dict(rates=rates, slow=slow))
    nav, drv = got.get('nav'), got.get('drv') or {}
    if nav is None:
        L.add('nav_status', 'FAIL', 'navigation publishes no status', dict(state=None))
    else:
        L.add('nav_status', 'PASS' if nav.get('ready') else 'WARN',
              f"navigation {nav.get('state')}" + (f", scan fits the map {nav.get('fit')}" if nav.get('fit') is not None else '') +
              (f" ({nav.get('why')})" if nav.get('why') else ''),
              dict(state=nav.get('state'), ready=bool(nav.get('ready')), fit=nav.get('fit'), tried=nav.get('tried'), heals=nav.get('heals')))
    L.add('driver', 'PASS' if drv else 'FAIL',
          f"wheels {'enabled' if drv.get('enabled') else 'disabled'}, e-stop {drv.get('estop')}, {'plugged in' if drv.get('plugged') else 'not plugged in'}"
          if drv else 'the board driver publishes no state',
          dict(enabled=drv.get('enabled'), estop=drv.get('estop'), plugged=drv.get('plugged')))
