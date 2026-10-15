"""Buddy's eyes = the robot's camera (owner 2026-10-01: "yo buddy, what do you see" + train on it).

robot_see(question): robot inspects (turns to the owner's hands / held object) -> frame + the robot's own detections
(robot_api /frame.jpg, /seen) -> Gemma (LM Studio, vision) answers -> one record saved for training.
teach_robot(label, correct): the owner confirms or corrects the last answer; the owner's word is the label.

Data (bigbuddy's own disk, decisions 2026-10-01): ~/rosorin-data/seen/YYYYMMDD/HHMMSS.{jpg,json};
every owner label is also appended to ~/rosorin-data/seen/labels.jsonl (the training set)."""
import base64, glob, json, os, queue, threading, time

import requests

ROBOT_URL = os.environ.get('BUDDY_ROBOT_URL', 'http://192.168.1.108:8296')
TOKEN_FILE = os.path.expanduser('~/.config/rosorin/api_token')
LLM_URL = os.environ.get('BUDDY_LLM_URL', 'http://localhost:1234/v1/chat/completions')
LLM_MODEL = os.environ.get('BUDDY_LLM_MODEL', 'google/gemma-4-12b')
DATA = os.path.expanduser('~/rosorin-data/seen')
INSPECT_WAIT_S = 1.5          # robot turns to the hands (mind INSPECT_S = 5 s) before the frame is taken


def _h():
    return {'X-Robot-Token': open(TOKEN_FILE).read().strip()}


def robot_see(question='what do you see?'):
    h = _h()
    requests.post(ROBOT_URL + '/heard', json={'event': 'inspect'}, headers=h, timeout=2)
    # wait until the robot has Matt and is looking at his hands (it may have to search the room first)
    found, end = False, time.time() + 15.0                 # a full room sweep takes ~15 s
    while time.time() < end:
        try:
            sn = requests.get(ROBOT_URL + '/seen', headers=h, timeout=2).json()
            if sn.get('mode') == 'track' and sn.get('inspecting'):
                found = True; break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(INSPECT_WAIT_S if found else 0.0)
    jpg = requests.get(ROBOT_URL + '/frame.jpg', headers=h, timeout=5).content
    seen = requests.get(ROBOT_URL + '/seen', headers=h, timeout=3).json()
    labels = sorted({d[0].replace('a ', '', 1) for d in seen.get('dets', [])}) or ['nothing specific']
    if not found:
        question += ' (Note: your robot body searched the room but could not find Matt; say so briefly.)'
    taught = taught_names()
    prompt = (f'This is your own camera view: you are Buddy, and this camera is on your robot body\'s arm. '
              f'Matt asked: "{question}". Your on-board detector reports: {", ".join(labels)} (it can be wrong). '
              + (f'Objects Matt has taught you by name: {", ".join(taught)}. ' if taught else '') +
              'A person in view is almost always Matt: call him Matt. Answer in one or two short spoken sentences. If Matt is holding something up, focus on that object '
              'and name it as specifically as you can. If you cannot identify the held object confidently because it is too '
              'small, too far, blurry or partly outside the picture, reply starting with CLOSER: followed by your best guess. '
              'Never guess a dangerous object (weapon) from a partial view.')
    r = requests.post(LLM_URL, json={'model': LLM_MODEL, 'max_tokens': 120, 'temperature': 0.2, 'reasoning_effort': 'none',
                                     'messages': [{'role': 'user', 'content': [
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'
                                                                             + base64.b64encode(jpg).decode()}},
                                         {'type': 'text', 'text': prompt}]}]}, timeout=60)
    r.raise_for_status()
    answer = (r.json()['choices'][0]['message'].get('content') or '').strip()
    closer = answer.upper().startswith('CLOSER:')            # owner 2026-10-01: robot may ask to bring it closer
    if closer:
        answer = answer.split(':', 1)[1].strip()
    day = time.strftime('%Y%m%d'); stamp = time.strftime('%H%M%S')
    d = os.path.join(DATA, day); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, stamp + '.jpg'), 'wb').write(jpg)
    json.dump({'t': time.time(), 'question': question, 'robot_dets': seen.get('dets', []),
               'robot_inspecting': seen.get('inspecting'), 'answer': answer, 'need_closer': closer, 'owner_label': None},
              open(os.path.join(d, stamp + '.json'), 'w'), indent=1)
    if closer:
        return (f'Not sure yet (best guess: {answer or "unknown"}). Ask Matt to bring it closer to your camera; '
                'when he does, call robot_see again.')
    return answer or 'I could not make it out.'


pending = queue.Queue()      # things Buddy should say later (robot_come arrival); spoken by the voice loop when idle


def robot_come(question='what is this?'):
    """'Come check this out': the robot drives itself to Matt (rosorin-come on the robot). Returns at once so Buddy
    keeps listening ("yo buddy, stop"); on arrival a background thread looks (robot_see) and queues what it saw."""
    r = requests.post(ROBOT_URL + '/task/come', headers=_h(), timeout=5).json()
    if not r.get('started'):
        return f'cannot come: {r.get("error", "refused")}'

    def wait():
        end, st = time.time() + 180, {}
        time.sleep(3)
        while time.time() < end:
            try:
                st = requests.get(ROBOT_URL + '/task', headers=_h(), timeout=3).json()
            except Exception:
                st = {}
            if st and not st.get('active'):
                break
            time.sleep(1.0)
        res = (st.get('last') or {})
        if res.get('ev') == 'summary' and res.get('result') == 'SUCCEEDED':
            try:
                time.sleep(2.0)                               # the mind finds Matt again
                pending.put(robot_see(question))
            except Exception as e:
                pending.put(f'I got there but could not look: {e}')
        else:
            pending.put('I could not get to you: ' + str(res.get('reason') or res.get('stopped_by') or res.get('result') or 'unknown'))
    threading.Thread(target=wait, daemon=True).start()
    return 'On my way over now; I will look when I get there.'


def robot_task(mode, done_msg):
    """home | sethome: start, return at once; the result is spoken when the robot finishes."""
    r = requests.post(ROBOT_URL + f'/task/{mode}', headers=_h(), timeout=5).json()
    if not r.get('started'):
        return f'cannot: {r.get("error", "refused")}'

    def wait():
        end, st = time.time() + 180, {}
        time.sleep(3)
        while time.time() < end:
            try:
                st = requests.get(ROBOT_URL + '/task', headers=_h(), timeout=3).json()
            except Exception:
                st = {}
            if st and not st.get('active'):
                break
            time.sleep(1.0)
        res = st.get('last') or {}
        ok = res.get('ev') == 'summary' and res.get('result') == 'SUCCEEDED'
        pending.put(done_msg if ok else 'That did not work: ' + str(res.get('reason') or res.get('stopped_by') or res.get('result') or 'unknown'))
    threading.Thread(target=wait, daemon=True).start()
    return 'On it.'


def robot_stop():
    r = requests.post(ROBOT_URL + '/stop', headers=_h(), timeout=5).json()
    return 'stopped' if r.get('ok') else f'stop failed: {r}'


SCANS = os.path.expanduser('~/rosorin-data/roomscans')
LEARN_AFTER, LEARN_CAP = 2, 40          # a missed object seen in >= 2 look-arounds is taught; detection stays light


def _norm(name):
    n = name.lower().strip()
    for a in ('a ', 'an ', 'the '):
        if n.startswith(a):
            n = n[len(a):]
    return n


def robot_look_around(question='what do you see?'):
    """'What do you see': the robot looks around the whole room (arm only, 6 views). Gemma = teacher: describes it
    and lists objects per view; objects the robot's own detector missed, seen in >= LEARN_AFTER look-arounds, are
    taught to the robot (live, capped). Every look-around is saved as labeled data."""
    h = _h(); t0 = time.time()
    requests.post(ROBOT_URL + '/heard', json={'event': 'scan_room'}, headers=h, timeout=2)
    meta, end = None, time.time() + 45                          # look-around ~26 s + settling
    while time.time() < end:
        time.sleep(1.0)
        try:
            m = requests.get(ROBOT_URL + '/roomscan', headers=h, timeout=3)
            if m.ok and m.json().get('t', 0) > t0:
                meta = m.json(); break
        except Exception:
            pass
    if not meta:
        return 'I could not finish looking around the room.'
    jpg = requests.get(ROBOT_URL + '/roomscan.jpg', headers=h, timeout=5).content
    robot_seen = sorted({_norm(d[0]) for sh in meta['shots'] for d in sh['dets']})
    focus = '; '.join(f"{sh['view']}: {('NEW ' if sh.get('new') else '') + (sh.get('focus') or 'general view').replace('a ', '', 1)}"
                      for sh in meta['shots'])
    prompt = (f'These are numbered close looks your robot body took while looking around the den on its own (you are '
              f'Buddy; the camera is on its arm). Each centres something that caught its interest - {focus}. Black '
              f'tiles are unused. Matt asked: "{question}". Mention what is new first. Reply ONLY with JSON: '
              '{"say": one or two short spoken sentences about what you see (a person is Matt), '
              '"objects": [{"name": short common noun, "view": 1-6}]} listing the distinct physical objects you can '
              'clearly identify (max 15).')
    r = requests.post(LLM_URL, json={'model': LLM_MODEL, 'max_tokens': 400, 'temperature': 0.2, 'reasoning_effort': 'none',
                                     'messages': [{'role': 'user', 'content': [
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'
                                                                             + base64.b64encode(jpg).decode()}},
                                         {'type': 'text', 'text': prompt}]}]}, timeout=90)
    r.raise_for_status()
    txt = (r.json()['choices'][0]['message'].get('content') or '').strip()
    try:
        d = json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
    except ValueError:
        d = {'say': txt, 'objects': []}
    teacher = sorted({_norm(o.get('name', '')) for o in d.get('objects', []) if o.get('name')})
    missed = [n for n in teacher if n and n not in robot_seen]
    os.makedirs(SCANS, exist_ok=True); stamp = time.strftime('%Y%m%d_%H%M%S')
    open(os.path.join(SCANS, stamp + '.jpg'), 'wb').write(jpg)
    json.dump({'t': time.time(), 'question': question, 'robot_scan': meta, 'teacher': d, 'robot_seen': robot_seen,
               'missed_by_robot': missed}, open(os.path.join(SCANS, stamp + '.json'), 'w'), indent=1)
    cf = os.path.join(SCANS, 'candidates.json')                   # training state: counts + what was taught
    try:
        st = json.load(open(cf))
    except (OSError, ValueError):
        st = {'counts': {}, 'taught': []}
    taught_now, counted = [], st.setdefault('scans', [])
    if meta.get('dir') in counted:                            # same robot scan answered twice: count it once
        missed = []
    else:
        counted.append(meta.get('dir'))
    for n in missed:
        st['counts'][n] = st['counts'].get(n, 0) + 1
        if st['counts'][n] >= LEARN_AFTER and n not in st['taught'] and len(st['taught']) < LEARN_CAP:
            try:
                requests.post(ROBOT_URL + '/heard', json={'event': 'learn', 'text': n}, headers=h, timeout=3)
                st['taught'].append(n); taught_now.append(n)
            except Exception:
                pass
    json.dump(st, open(cf, 'w'), indent=1)
    say = d.get('say') or 'I looked around but could not describe it.'
    return say + (f' (I just learned to spot: {", ".join(taught_now)}.)' if taught_now else '')


def taught_names(n=30):
    """Owner-taught object names, newest first (text only: no example images, no extra GPU work per question)."""
    out = []
    try:
        for line in reversed(open(os.path.join(DATA, 'labels.jsonl')).read().splitlines()):
            lab = json.loads(line)['label']
            if lab and lab not in out:
                out.append(lab)
    except (OSError, ValueError):
        pass
    return out[:n]


def teach_robot(label, correct=False):
    """Attach the owner's label to the latest robot_see record (within 5 min)."""
    recs = sorted(glob.glob(os.path.join(DATA, '*', '*.json')))
    if not recs:
        return 'nothing to label: no robot_see yet'
    f = recs[-1]; rec = json.load(open(f))
    if time.time() - rec['t'] > 300:
        return 'nothing recent to label (last look was over 5 minutes ago)'
    rec['owner_label'] = label.strip(); rec['owner_confirmed_answer'] = bool(correct); rec['labelled_at'] = time.time()
    json.dump(rec, open(f, 'w'), indent=1)
    with open(os.path.join(DATA, 'labels.jsonl'), 'a') as out:
        out.write(json.dumps({'image': os.path.relpath(f[:-5] + '.jpg', DATA), 'label': rec['owner_label'],
                              'answer': rec['answer'], 'robot_dets': rec['robot_dets'], 't': rec['labelled_at']}) + '\n')
    try:                                                      # the robot's detector learns the word now (live)
        requests.post(ROBOT_URL + '/heard', json={'event': 'learn', 'text': rec['owner_label']}, headers=_h(), timeout=3)
    except Exception:
        pass
    return f'saved: "{rec["owner_label"]}" as training data; the robot looks for it from now on'
