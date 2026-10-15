import base64, glob, json, os, time, requests, subprocess, re
f = sorted(glob.glob(os.path.expanduser('~/vision/think/*/*.jpg')))[-1]
b64 = base64.b64encode(open(f, 'rb').read()).decode()
ts = subprocess.Popen(['tegrastats', '--interval', '500'], stdout=subprocess.PIPE, text=True)
lat, toks = [], []
for i in range(5):
    t = time.time()
    r = requests.post('http://localhost:8000/v1/chat/completions', json={'model': 'brain', 'max_tokens': 120, 'temperature': 0.3,
        'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
                                                  {'type': 'text', 'text': 'Describe what you see in one sentence, then list the objects.'}]}]}, timeout=60).json()
    lat.append(time.time() - t); toks.append(r['usage']['completion_tokens'])
ts.terminate(); out = ts.stdout.read()
gpu = [int(x) for x in re.findall(r'GR3D_FREQ (\d+)%', out)]; pw = [int(x) for x in re.findall(r'VDD_IN (\d+)mW', out)]
print('latency s', [round(x, 2) for x in lat], '| tokens', toks, '| tok/s', round(sum(toks[1:]) / sum(lat[1:]), 1))
print('GPU busy avg %d%% | board power %.1f W' % (sum(gpu) / len(gpu), sum(pw) / len(pw) / 1000))
print('answer:', r['choices'][0]['message']['content'][:200])
