"""Generate the two-timebar datasets and dashboard payloads into data/twin2/.

Usage: python scripts/simulate_twin2.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.twin2.build import OUT_DIR, build  # noqa: E402


def main() -> None:
    out = build()
    for n in (1, 2):
        events = out[f"timebar{n}"]["events"]
        print(f"Timebar {n}: {len(events)} events  {dict(Counter(e['type'] for e in events))}")
    print(f"Written to {OUT_DIR}: " + ", ".join(sorted(p.name for p in OUT_DIR.iterdir())))


if __name__ == "__main__":
    main()
