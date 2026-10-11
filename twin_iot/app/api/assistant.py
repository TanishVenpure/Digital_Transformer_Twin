"""Chat grounded in this prototype's two-timebar data, not an unrelated backend."""
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agent.chat import explain, get_client
from app.agent.recorded import _monitor_states, _deterministic_answer
from app.twin2 import sheet

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transformer_id: str = sheet.TRANSFORMER_ID
    question: str = Field(min_length=1, max_length=2000)
    until: str | None = Field(default=None, max_length=40)
    until2: str | None = Field(default=None, max_length=40)


def context(body):
    if body.transformer_id != sheet.TRANSFORMER_ID:
        raise HTTPException(404, "This prototype only exposes CPRI-630 demo evidence")
    checks, tb1, tb2 = sheet.load_sheet()
    try:
        cur = sheet.cursors(tb1, tb2, body.until, body.until2)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid playback cursor") from None
    checks = sheet.sheet_at(checks, cur)
    # Explicit cap keeps outbound context bounded. No notes, messages or future resolution data.
    evidence = [{"source_id": f"check:{c['check_no']}", "content": {
        k: c[k] for k in ("timebar", "iot_name", "signal", "flag", "opened_at", "resolved_at", "status")}}
        for c in checks[:30]]
    for n, payload, index in ((1, tb1, cur["minute"]), (2, tb2, cur["day"])):
        content = {"timestamp": cur[n].isoformat(), "synthetic": True,
                   "statuses": {key: values[index] for key, values in payload["status"].items()}}
        # Select current numeric values only; never forward entire histories/event windows.
        content["measurements"] = {key: values[index] for key, values in payload["series"].items()
            if isinstance(values, list) and index < len(values)
            and isinstance(values[index], (int, float, bool, type(None)))}
        evidence.append({"source_id": f"timebar:{n}", "content": content})
    cursor = {"until": cur[1].isoformat(), "until2": cur[2].date().isoformat(),
              "timebar1": cur[1].astimezone(timezone.utc).isoformat(),
              "timebar2": cur[2].date().isoformat(), "minute": cur["minute"], "day": cur["day"]}
    monitors = _monitor_states(tb1, tb2, cur)
    for m in monitors:
        evidence.append({"source_id": f"monitor:{m['timebar']}:{m['monitor']}", "content": m})
    energized = tb1["series"].get("energized", [])
    ctx = {"checks": checks, "monitors": monitors, "cursor": cursor,
           "energized": bool(energized[cur["minute"]]) if energized else True}
    return evidence, cursor, ctx


@router.get("/status")
def status():
    import os
    return {"provider": os.getenv("LLM_PROVIDER", "none"),
            "credentials_configured": bool(os.getenv("GEMINI_API_KEY")),
            "source": "two-timebar prototype demo data", "health_score": "not_implemented"}


@router.post("/chat")
def chat(body: ChatIn, client=Depends(get_client)):
    evidence, cursor, ctx = context(body)
    recorded = _deterministic_answer(body.question, ctx, body.transformer_id)
    return {"transformer_id": body.transformer_id, "cursor": cursor,
            **explain(body.question, evidence, cursor, client, recorded=recorded)}
