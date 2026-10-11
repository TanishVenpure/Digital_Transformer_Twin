# Prototype LLM assistant integration

Target branch: prototype. Source: prototype-withchatbot-twin2, commit 38bc982.
The dashboard assistant on the source branch was deterministic; its actual Gemini
client lived in the root backend. This integration reuses that client in
`twin_iot/app/agent/llm.py` and adapts evidence to the prototype's existing two-timebar data.
It does not import the old risk engine, health score, twin2 formulas, or simulated datasets.

## Run

Install `twin_iot/requirements.txt`. Start from `twin_iot`:

```powershell
$env:LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "<set securely in your local environment>"
$env:GEMINI_MODEL = "gemini-2.5-flash"
$env:GEMINI_THINKING_LEVEL = "default"
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8021
```

Default `LLM_PROVIDER=none` makes no provider calls and uses the source branch recorded-data conversation behavior.
The API key stays on the server; it is not stored in source, sent to the browser, or
returned by status. No credentials are copied from another checkout. A missing key,
provider timeout or failed evidence assertion falls back to the same topic-specific recorded-data answers.
No live provider call has been verified without a user-configured key.

## Data and API

- `POST /api/assistant/chat`: question, transformer_id (CPRI-630), until (timebar 1 ISO
  timestamp), until2 (timebar 2 ISO date).
- `GET /api/assistant/status`: configured provider and credential-presence boolean.
- Responses contain answer, answer_source, facts, evidence_source_ids, warnings and cursors.
- Browser uses both current timebar cursors, rendering response text and expandable facts with textContent.
- Recorded-data chat preserves the source branch greetings, topic-specific monitor answers,
  signal counts, maintenance summary, recorded repairs and out-of-domain response.
  Only monitors present in this prototype are available; missing source-only monitors are not added.
- Evidence is synthetic prototype data, not the separate PostgreSQL T1 hourly timeline.
  Each response explicitly identifies that provenance. It never claims these are live readings.
- Gemini receives the question, selected current scalar measurements/statuses and at most
  30 recent check summaries, plus derived monitor display values (including precision arrays reduced to their recorded maximum). Entire datasets, raw event messages, secrets and future repair
  outcomes are excluded. Enabling Gemini sends that bounded evidence to Google.
- Source IDs and exact asserted field values are checked. Free-form prose and physical
  diagnoses are not fully validated by those checks; human review remains necessary.
- No maintenance-risk index or transformer health-score calculation is introduced.

## Tests

Tests cover source Gemini SDK wiring with a fake transport, valid structured replies,
wrong values/citations, invented scores, provider timeout, no credentials mode,
scope restrictions, invalid cursor input and prevention of future resolution leakage.
Other prototype tests continue to exercise the existing model pipeline.

## Behavior correction

The first integration used a generic five-check fallback, which did not reproduce the
source dashboard assistant. `app/agent/recorded.py` now reuses that branch conversation
logic without its risk assessment. All visible checks feed local counts and repair
answers; provider evidence remains bounded. Cursor metadata and expandable fact details
are restored. No formula, threshold, processing rule or simulated dataset was changed.

## Recorded maintenance popups

The dashboard now includes the source-branch maintenance popups. These are existing
rule/event records, not LLM-generated alerts. `GET /api/twin2/sheet` adapts the existing
playback events to checks without creating a new table or evaluating new rules.
`maint-events.js` tracks playback crossings; `maintenance-popups.js` renders the
recorded message, severity, timestamp and monitor link. Each timebar advances independently.
Rewind retracts future popups; replay shows them again. Exact opening timestamps prevent
showing an alert before its recorded second. Dismiss and automatic expiry affect only the
popup, not the stored alert or acknowledgement. No Gemini credentials are required.
