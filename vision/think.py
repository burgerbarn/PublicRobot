"""The robot's thinking - served by the BRAIN on bigbuddy (vision-language model, OpenAI API; LM Studio Gemma 4 12B).
Runs in a thread inside the mind. Every PERIOD s (awake) / SLEEP_PERIOD s (asleep) it shows the model the current
camera frame + a short state summary and asks for ONE skill from a fixed menu, as JSON. The model never commands
motors: the mind executes skills through its own safe paths (joint_jog + driver gates + arm-model veto).
If the brain is down or slow, the mind carries on without thoughts (reflexes and perception never wait on it).
Every thought is logged with its frame (~/vision/think/<UTC day>/) = the data the teacher (Claude) reviews and labels
to train the robot's models (distillation); questions for the owner go to ~/vision/questions.jsonl."""
import base64, json, os, threading, time

import cv2
import numpy as np
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
# The brain lives on bigbuddy (owner 2026-09-30: bigbuddy + server = brain/knowledge; robot = nav/vision).
URL = os.environ.get('BRAIN_URL', 'http://192.168.1.111:1234/v1/chat/completions')
MODEL = os.environ.get('BRAIN_MODEL', 'google/gemma-4-12b')
PERIOD, SLEEP_PERIOD, TIMEOUT = 4.0, 60.0, 30.0
SCENE_DIFF, MAX_QUIET = 6.0, 60.0           # grey levels (0-255) of change that count as a new scene; s
SKILLS = {
    'keep_watching': 'keep doing what you are doing (the default)',
    'look_toward': 'turn the head toward a direction: args {"direction": "left|right|up|down|center"}',
    'look_around': 'slowly look around the room for something new',
    'identify': 'you see something worth remembering: args {"object": "<what it is>", "where": "<where in the image>"}',
    'ask_owner': 'you are unsure about something and want the owner to tell you: args {"question": "<short>"}',
    'rest': 'nothing to do: rest (sleep) for a while',
    # the body is yours (owner 2026-10-02: control is delegated to the robot; Claude builds and trains, never drives)
    'practice_looking': 'practise looking around with your arm to learn what each direction shows you (arm only, any time)',
    'practice_moving': 'practise small moves with your wheels to learn what your body does on this floor '
                       '(only when state.can_drive; a few minutes, stays near where you are)',
    'explore_room': 'drive around the room to map it and see what is there '
                    '(only when state.can_drive and state.ready_to_explore)',
}
PROMPT = """You are the mind of a small home robot: part of "Buddy", the owner's assistant. You live in the owner's
den, on a rug. Your camera is on your arm, so where you look is visible to everyone. You are highly curious: you seek out
what you don't know, you never guess - if unsure, you ask the owner. Your body is yours to use: you move your head any
time, and you drive when state.can_drive is true. Nobody drives you. When the owner is not asking anything of you, you
decide how to use the time - looking, practising, exploring or resting - and you learn from what happens. If the owner
says stop or stay, you stay.
Current state: {state}
Look at the camera image and choose ONE skill:
{skills}
Reply with ONLY JSON: {{"thought": "<one short sentence: what you notice / why>", "skill": "<name>", "args": {{...}}, "confidence": <0-1>}}"""


class Thinker:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame, self.state, self.asleep = None, {}, False
        self.latest = None                                    # (time, decision dict)
        self.day, self.log, self.dir = None, None, None
        self.ok_count = self.fail_count = 0
        threading.Thread(target=self.run, daemon=True).start()

    def feed(self, rgb, state, asleep):
        with self.lock:
            self.frame, self.state, self.asleep = rgb, dict(state), asleep

    def take(self):
        """The newest decision not yet taken (or None)."""
        with self.lock:
            d, self.latest = self.latest, None
            return d

    def _logfile(self):
        day = time.strftime('%Y%m%d', time.gmtime())
        if day != self.day:
            self.day = day
            self.dir = os.path.join(HERE, 'think', day); os.makedirs(self.dir, exist_ok=True)
            self.log = open(os.path.join(self.dir, 'thoughts.jsonl'), 'a')
        return self.log

    def run(self):
        next_t = time.time() + 5.0
        while True:
            time.sleep(0.5)
            with self.lock:
                frame, state, asleep = self.frame, self.state, self.asleep
            if frame is None or time.time() < next_t:
                continue
            next_t = time.time() + (SLEEP_PERIOD if asleep else PERIOD)
            # efficiency (owner 2026-10-01): the same scene gave the same thought every 4 s. Think only when the view
            # changed (mean abs diff of a 32x24 grey thumbnail) or MAX_QUIET s passed since the last thought.
            thumb = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (32, 24), interpolation=cv2.INTER_AREA).astype(np.int16)
            last = getattr(self, 'last_thumb', None)
            if last is not None and np.abs(thumb - last).mean() < SCENE_DIFF and time.time() - self.last_think < MAX_QUIET:
                continue
            self.last_thumb, self.last_think = thumb, time.time()
            small = cv2.resize(frame, (448, int(448 * frame.shape[0] / frame.shape[1])))
            jpg = cv2.imencode('.jpg', cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            prompt = PROMPT.format(state=json.dumps(state), skills='\n'.join(f'- {k}: {v}' for k, v in SKILLS.items()))
            body = {'model': MODEL, 'max_tokens': 160, 'temperature': 0.3, 'reasoning_effort': 'none',   # Gemma 4: no hidden thinking
                    'messages': [{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode()}},
                        {'type': 'text', 'text': prompt}]}]}
            t0 = time.time()
            try:
                txt = requests.post(URL, json=body, timeout=TIMEOUT).json()['choices'][0]['message']['content'] or ''
                d = json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
                if d.get('skill') not in SKILLS:
                    raise ValueError(f'unknown skill {d.get("skill")}')
                d['latency_s'] = round(time.time() - t0, 2)
                self.ok_count += 1
            except Exception as e:
                self.fail_count += 1
                d = {'error': f'{type(e).__name__}: {str(e)[:120]}', 'latency_s': round(time.time() - t0, 2)}
            fn = time.strftime('%H%M%S', time.gmtime()) + '.jpg'
            log = self._logfile()
            with open(os.path.join(self.dir, fn), 'wb') as f:
                f.write(jpg)
            log.write(json.dumps({'t': round(time.time(), 2), 'state': state, 'frame': fn, **d}) + '\n'); log.flush()
            if 'error' in d:
                continue
            if d['skill'] == 'ask_owner':
                with open(os.path.join(HERE, 'questions.jsonl'), 'a') as f:
                    f.write(json.dumps({'t': round(time.time(), 2), 'question': d.get('args', {}).get('question'),
                                        'frame': os.path.join('think', self.day, fn)}) + '\n')
            with self.lock:
                self.latest = (time.time(), d)
