"""
Scan filter module: applies per-label score floor and minimum views thresholds to merged scan objects from 20260929T090320Z.
Objects are kept if their score meets or exceeds the floor and they have at least the minimum number of views; otherwise they are dropped.
The thresholds are defined per label below, with a default for unknown labels.
"""

LABEL_FILTER = {
    "a microphone": (0.15, 1),
    "a television": (0.5, 2),
    "a speaker": (0.5, 2)
}
DEFAULT_FILTER = (0.3, 2)

def filter_objects(objs):
    kept = []
    dropped = []
    for o in objs:
        label = o.get("label")
        floor, min_views = LABEL_FILTER.get(label, DEFAULT_FILTER)
        if o.get("score", 0) >= floor and o.get("views", 0) >= min_views:
            kept.append(o)
        else:
            dropped.append(o)
    return kept, dropped
