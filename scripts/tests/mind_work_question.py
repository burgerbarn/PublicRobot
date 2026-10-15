import base64, json, os, sys, requests
sys.path.insert(0, os.path.expanduser('~/vision'))
import think
D = os.path.expanduser('~/vision/think/20261005')
meta = json.load(open('/tmp/label_meta.json'))
Q = ('This is the view from a small robot on the floor. If a person is in view: what are their hands doing, what are they working on or holding, '
     'and where in the image is it (left/centre/right, top/middle/bottom)? Can you see the thing itself or only that they are busy with something out of view? '
     'Reply with ONLY JSON: {"person": true|false, "working_on": "<short>", "thing_visible": true|false, "where": "<position>"}')
import collections
for i in (1, 5, 26, 29, 21, 45, 24, 9, 46, 40):
  ans = []
  for rep in range(5):
    m = meta[i]; jpg = open(D + '/' + m['frame'], 'rb').read()
    body = {'model': think.MODEL, 'max_tokens': 120, 'temperature': 0.9, 'reasoning_effort': 'none', 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode()}}, {'type': 'text', 'text': Q}]}]}
    try:
        txt = requests.post(think.URL, json=body, timeout=40).json()['choices'][0]['message']['content']
        ans.append(str(json.loads(txt[txt.index('{'):txt.rindex('}') + 1]).get('working_on', '?')).lower()[:28])
    except Exception as e:
        ans.append('error')
  c = collections.Counter(ans); print(i, 'top answer share', round(c.most_common(1)[0][1] / len(ans), 2), dict(c))
