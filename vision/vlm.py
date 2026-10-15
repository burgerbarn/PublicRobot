"""Vision-language model client (Gemma 4 12B on bigbuddy's LM Studio) shared by everything on the robot.
Failures seen 2026-09-29 ("no answer: KeyError" = an error body without 'choices'); a cold load itself takes ~2 s
and works, so they are most likely a model swap or load contention (unverified). Retries with backoff long enough
to ride out a swap, and LOGS the error body (~/vision/vlm_errors.log) so the cause shows up in data.
ask(prompt, image_bgr=None) -> (text or None, error or None)."""
import base64, json, os, time

import cv2
import requests

URL = os.environ.get('VLM_URL', 'http://192.168.1.111:1234/v1/chat/completions')
MODEL = os.environ.get('VLM_MODEL', 'google/gemma-4-12b')
BACKOFF = (2.0, 5.0, 10.0)
ERRLOG = os.path.expanduser('~/vision/vlm_errors.log')


def ask(prompt, image_bgr=None, max_tokens=120, timeout=60):
    content = [{'type': 'text', 'text': prompt}]
    if image_bgr is not None:
        b64 = base64.b64encode(cv2.imencode('.jpg', image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()).decode()
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b64}})
    body = {'model': MODEL, 'max_tokens': max_tokens, 'temperature': 0, 'reasoning_effort': 'none',   # Gemma 4 thinks
            'messages': [{'role': 'user', 'content': content}]}                                         # otherwise -> empty
    err = None
    for i, wait in enumerate((0.0,) + BACKOFF):
        time.sleep(wait)
        try:
            r = requests.post(URL, json=body, timeout=timeout)
            j = r.json()
            if 'choices' in j:
                return (j['choices'][0]['message'].get('content') or '').strip(), None
            err = f'HTTP {r.status_code}: {json.dumps(j)[:300]}'
        except Exception as e:
            err = f'{type(e).__name__}: {str(e)[:200]}'
        try:
            with open(ERRLOG, 'a') as f:
                f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} attempt {i + 1}: {err}\n')
        except OSError:
            pass
    return None, err


def ask_json(prompt, image_bgr=None, **kw):
    """ask() and parse the first {...} in the reply; (dict or None, error or None)."""
    txt, err = ask(prompt, image_bgr, **kw)
    if txt is None:
        return None, err
    try:
        return json.loads(txt[txt.index('{'):txt.rindex('}') + 1]), None
    except ValueError:
        return None, f'unparsable: {txt[:120]}'
