import base64, collections, json, os, sys
import requests
sys.path.insert(0, os.path.expanduser('~/vision'))
import think
D = os.path.expanduser('~/vision/think/20261005')
rows = []
for l in open(D + '/thoughts.jsonl'):
    try: t = json.loads(l)
    except ValueError: continue
    if t.get('frame') and os.path.exists(D + '/' + t['frame']) and (t.get('state') or {}).get('owner_said'): rows.append(t)
print(len(rows), 'logged moments where the owner had just said something')
seen = collections.OrderedDict()
for t in rows: seen.setdefault(str(t['state']['owner_said'])[:60], t)
pick = list(seen.values())[:12]
SK = {k: v for k, v in think.SKILLS.items() if k != 'identify'}
SK['greet'] = 'acknowledge someone: a small friendly head gesture toward them while Buddy says hello (when the owner asks you to say hi, or introduces someone)'
SK['answer_owner'] = 'the owner asked YOU something about what you see or can do: answer through Buddy. args {"answer": "<short>"}'
def ask(t, sk):
    prompt = think.PROMPT.format(state=json.dumps(t['state']), skills='\n'.join(f'- {k}: {v}' for k, v in sk.items()))
    jpg = open(D + '/' + t['frame'], 'rb').read()
    body = {'model': think.MODEL, 'max_tokens': 160, 'temperature': 0.3, 'reasoning_effort': 'none', 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode()}}, {'type': 'text', 'text': prompt}]}]}
    try:
        txt = requests.post(think.URL, json=body, timeout=40).json()['choices'][0]['message']['content'] or ''
        d = json.loads(txt[txt.index('{'):txt.rindex('}') + 1]); return d.get('skill'), json.dumps(d.get('args') or {})[:70]
    except Exception as e:
        return 'ERROR', str(e)[:40]
for t in pick:
    a = ask(t, think.SKILLS); b = ask(t, SK)
    print(f"owner said: {str(t['state']['owner_said'])[:44]:46s} | logged: {t['skill']:13s} | menu today: {a[0]:13s} | menu with greet/answer: {b[0]} {b[1] if b[0] in ('answer_owner', 'ask_owner') else ''}")
