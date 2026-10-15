import json, pathlib
from scan_filter import filter_objects

def main():
    data_path = pathlib.Path(__file__).parent / 'testdata' / 'scan_20260929T090320Z.json'
    with open(data_path) as f:
        d = json.load(f)
    kept, dropped = filter_objects(d['objects'])
    print("KEPT")
    for o in kept:
        print(f"{o['label']}, {round(o['score'],3)}, {o['views']}, {o['bearing_deg']}")
    print("DROPPED")
    for o in dropped:
        print(f"{o['label']}, {round(o['score'],3)}, {o['views']}, {o['bearing_deg']}")

if __name__ == "__main__":
    main()
