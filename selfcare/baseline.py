"""What is normal for THIS robot: the median of its own healthy measurements (last 300 per value). Until a value has
30 samples the seed from expected.json is used."""
import os

from common import DATA, load, save

PATH = os.path.join(DATA, 'baseline.json')


class Baseline:
    def __init__(self):
        self.d = load(PATH, {})

    def usual(self, key, seed):
        v = self.d.get(key, [])
        return sorted(v)[len(v) // 2] if len(v) >= 30 else seed

    def learn(self, values):
        for k, x in values.items():
            if isinstance(x, (int, float)):
                self.d[k] = (self.d.get(k, []) + [round(float(x), 2)])[-300:]
        save(PATH, self.d)
