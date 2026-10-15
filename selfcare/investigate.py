"""For findings no rule explains: the brain (the model on bigbuddy the mind already uses) chooses which further
read-only check to run, one at a time, and names a cause citing evidence ids. Code runs the checks, refuses repeats
and unknown names, and throws away any cause that cites evidence that does not exist. The brain never acts.
Tested 2026-10-05 with the day's real symptoms: valid choices in ~1 s each (docs/research/doing_it_itself_reference.md)."""
import json, os

import checks

URL = os.environ.get('BRAIN_URL', 'http://192.168.1.111:1234/v1/chat/completions')
MODEL = os.environ.get('BRAIN_MODEL', 'google/gemma-4-12b')
MENU = {
    'cpu_by_thread': 'for one process: which threads use the CPU and whether they are busy-waiting. args {"process": name}',
    'journal_errors': 'error lines of one of my services since boot. args {"service": "rosorin-nav|rosorin-base|rosorin-camera|rosorin-vslam|rosorin-mind|rosorin-contact|rosorin-api"}',
    'network': 'traffic between my own programs that goes through the kernel, and dropped packets',
    'resources': 'disk, memory, temperature',
    'run_outcomes': 'results of my last drives',
    'battery': 'battery voltage, trend, plugged in or not',
    'done': 'enough evidence. args {"cause": "<one sentence>", "evidence": ["E1", ...]}',
    'unknown': 'the evidence does not show a cause',
}
SYS = ('You are the robot ROSOrin examining a problem with yourself. You get numbered evidence. Choose ONE next step from '
       'this list only:\n' + '\n'.join(f'- {k}: {v}' for k, v in MENU.items()) +
       '\nDo not repeat a check that is already in the evidence. Answer with JSON only: '
       '{"check": name, "args": {...}, "why": "<short, citing evidence ids>"}')


def investigate(L, finding, exp, steps=4):
    try:
        import requests
    except ImportError:
        return None
    done = set()
    for _ in range(steps):
        user = f"Problem: {finding['what']}. Evidence: {L.brief()}"[:6000]
        try:
            r = requests.post(URL, timeout=30, json={'model': MODEL, 'temperature': 0.2, 'max_tokens': 200, 'reasoning_effort': 'none',
                              'messages': [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': user}]}).json()
            t = r['choices'][0]['message']['content']; d = json.loads(t[t.index('{'):t.rindex('}') + 1])
        except Exception as e:
            return dict(cause=None, note=f'brain not usable: {type(e).__name__}')
        c, a = d.get('check'), d.get('args') or {}
        key = (c, json.dumps(a, sort_keys=True))
        if c == 'done':
            ids = {e['id'] for e in L.items}; cited = [x for x in a.get('evidence', []) if isinstance(x, str)]
            if cited and all(x in ids for x in cited):
                return dict(cause=str(a.get('cause', ''))[:240], evidence=cited)
            return dict(cause=None, note='the brain named a cause without valid evidence')
        if c == 'unknown' or c not in MENU or key in done:
            return dict(cause=None, note='no cause found' if c == 'unknown' else f'stopped: {c} not usable or repeated')
        done.add(key)
        if c == 'cpu_by_thread':
            checks.cpu_by_thread(L, str(a.get('process', '')))
        elif c == 'journal_errors':
            s = str(a.get('service', ''))
            if s in exp['units']: checks.journal_errors(L, s)
            else: return dict(cause=None, note='stopped: unknown service')
        elif c == 'network': checks.network(L, exp)
        elif c == 'resources': checks.resources(L, exp)
        elif c == 'run_outcomes': checks.run_outcomes(L)
        elif c == 'battery': checks.battery(L, exp)
    return dict(cause=None, note='no cause within the step limit')
