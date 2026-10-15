import base64, collections, json, os, random, sys
import requests
sys.path.insert(0, os.path.expanduser('~/vision'))
import think
D = os.path.expanduser('~/vision/think/20261005'); random.seed(23)
rows = []
for l in open(D + '/thoughts.jsonl'):
    try: t = json.loads(l)
    except ValueError: continue
    if t.get('frame') and os.path.exists(D + '/' + t['frame']) and t.get('skill') and (t.get('state') or {}).get('mode') != 'track': rows.append(t)
pick = random.sample(rows, 30)
SK = {k: v for k, v in think.SKILLS.items() if k != 'identify'}
def ask(t, extra):
    st = dict(t['state']); st.update(extra)
    prompt = think.PROMPT.format(state=json.dumps(st), skills='\n'.join(f'- {k}: {v}' for k, v in SK.items()))
    jpg = open(D + '/' + t['frame'], 'rb').read()
    body = {'model': think.MODEL, 'max_tokens': 160, 'temperature': 0.3, 'reasoning_effort': 'none', 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode()}}, {'type': 'text', 'text': prompt}]}]}
    try:
        txt = requests.post(think.URL, json=body, timeout=40).json()['choices'][0]['message']['content'] or ''
        return json.loads(txt[txt.index('{'):txt.rindex('}') + 1]).get('skill')
    except Exception:
        return 'ERROR'
res = {}
for name, extra in (('run A', {}), ('run B (same again)', {}),
                    ('told: last 3 look-arounds found nothing new', {'last_look_arounds': 'the last 3 look-arounds here found nothing new'}),
                    ('told: last look-around found 4 new things', {'last_look_arounds': 'the last look-around here found 4 things you had not seen'}),
                    ('told: 2 h since practice, body model still changing', {'practice': 'your last body practice was 2 hours ago and what you learn about your wheels is still changing a lot', 'can_drive': True, 'plugged': False})):
    res[name] = [ask(t, extra) for t in pick]
    print(f'{name:52s}', dict(collections.Counter(res[name]).most_common()))
a, b = res['run A'], res['run B (same again)']
print('same choice in both identical runs:', sum(x == y for x, y in zip(a, b)), 'of', len(a))
