# Digital Transformer Twin

A digital twin of the CPRI 630 kVA, 11 / 0.433 kV ONAN distribution transformer: a 3D model and two
playback timebars (one day of fast signals, one year of daily readings) with every warning, trip,
analytics flag and maintenance action, plus an evidence-grounded assistant.

| Folder | What it is |
|---|---|
| `dashboard/` | The web page: 3D model, monitor cards, graphs, signals, maintenance popups, assistant |
| `twin_iot/` | FastAPI backend: the two-timebar model (`app/twin2/`), the assistant (`app/agent/`) |
| `docs/` | Assistant hand-off notes |

**Run it** (Python 3.11+, no database):

```
cd twin_iot
pip install -r requirements.txt
python -m uvicorn app.api.main:app --port 8020
```

Open http://localhost:8020/dashboard/. What every part does: [FEATURES.md](FEATURES.md).
