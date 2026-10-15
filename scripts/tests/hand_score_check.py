import json, os, sys
sys.path.insert(0, os.path.expanduser('~/vision'))
import PIL.Image
from scan import make_detector
D = os.path.expanduser('~/vision/think/20261005'); meta = json.load(open('/tmp/label_meta.json'))
hands = [1, 2, 5, 6, 10, 21, 24, 26, 29, 40, 41, 45, 46, 47]; nohand = [3, 4, 7, 8, 9, 18, 19, 23, 25, 30, 31, 32, 33, 36, 39]; empty = [0, 11, 12, 13, 14, 15, 16, 17, 20, 22, 27, 28, 34, 35, 37, 38, 42, 43, 44]
det = make_detector('nanoowl', '32', ['a face', 'a person', 'a hand', 'a cup', 'a phone', 'a bag', 'a box', 'a cable', 'a shoe', 'a guitar', 'a laptop'])
sc = {}
for i in hands + nohand + empty:
    d = [x[1] for x in det(PIL.Image.open(D + '/' + meta[i]['frame']).convert('RGB'), 0.05) if x[0] == 'a hand']
    sc[i] = max(d) if d else 0.0
for th in (0.10, 0.12, 0.15, 0.18, 0.22):
    print(f'threshold {th:.2f}: hand frames {sum(sc[i] >= th for i in hands)}/{len(hands)} | person but no hand in view {sum(sc[i] >= th for i in nohand)}/{len(nohand)} | nobody {sum(sc[i] >= th for i in empty)}/{len(empty)}')
print('scores, hand frames:', sorted(round(sc[i], 2) for i in hands)); print('scores, person without a visible hand:', sorted(round(sc[i], 2) for i in nohand)); print('scores, nobody:', sorted(round(sc[i], 2) for i in empty)[-6:])
