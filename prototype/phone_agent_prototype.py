#!/usr/bin/env python3
"""
Phone Agent Prototype (single-file)

A minimal local orchestrator API for Termux/Ubuntu environments.
- Accepts tasks
- Applies basic policy checks
- Writes artifacts to handoff/
- Emits JSONL audit logs
- Supports emergency kill switch
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hashlib
import json
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


BASE_DIR = Path(os.environ.get("AGENT_BASE", Path.home() / "agent-prototype"))
HANDOFF_DIR = BASE_DIR / "handoff"
LOGS_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
POLICY_FILE = BASE_DIR / "policy.json"
KILL_FILE = STATE_DIR / "killswitch.on"

DEFAULT_POLICY = {
    "allowed_domains": ["example.org", "example.com"],
    "allowed_actions": ["scan_page", "open_app", "tap", "swipe", "save_note"],
    "max_events_per_minute": 60,
    "require_confirm_for": ["download_file", "system_settings_change"],
}


def ensure_dirs() -> None:
    for d in (HANDOFF_DIR, LOGS_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_policy() -> dict[str, Any]:
    if not POLICY_FILE.exists():
        POLICY_FILE.write_text(json.dumps(DEFAULT_POLICY, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_POLICY
    return json.loads(POLICY_FILE.read_text(encoding="utf-8"))


def audit(event: dict[str, Any]) -> None:
    line = json.dumps(event, ensure_ascii=False)
    with (LOGS_DIR / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def safe_task_id(task_id: str) -> str:
    keep = "-_."
    cleaned = "".join(ch for ch in task_id if ch.isalnum() or ch in keep).strip("._")
    return cleaned or "task"


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


def assert_not_killed() -> None:
    if KILL_FILE.exists():
        raise HTTPException(status_code=423, detail="Kill switch is active")


class TaskRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=64)
    url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


app = FastAPI(title="Phone Agent Prototype", version="0.1.0")


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    load_policy()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "time": utc_now_iso(),
        "base_dir": str(BASE_DIR),
        "killswitch": KILL_FILE.exists(),
    }


@app.post("/killswitch/on")
def killswitch_on() -> dict[str, Any]:
    ensure_dirs()
    KILL_FILE.write_text(utc_now_iso(), encoding="utf-8")
    audit({"timestamp_utc": utc_now_iso(), "event": "killswitch_activated", "result": "on"})
    return {"status": "enabled"}


@app.post("/killswitch/off")
def killswitch_off() -> dict[str, Any]:
    if KILL_FILE.exists():
        KILL_FILE.unlink()
    audit({"timestamp_utc": utc_now_iso(), "event": "killswitch_activated", "result": "off"})
    return {"status": "disabled"}


@app.post("/task")
def submit_task(req: TaskRequest) -> dict[str, Any]:
    ensure_dirs()
    assert_not_killed()

    policy = load_policy()
    event_base = {
        "timestamp_utc": utc_now_iso(),
        "task_id": req.task_id,
        "action": req.action,
    }

    if req.action not in policy.get("allowed_actions", []):
        audit({**event_base, "event": "policy_check_failed", "reason": "action_not_allowed"})
        raise HTTPException(status_code=403, detail="Action not allowed")

    if req.url and not domain_allowed(req.url, policy.get("allowed_domains", [])):
        audit({**event_base, "event": "policy_check_failed", "reason": "domain_not_allowed", "url": req.url})
        raise HTTPException(status_code=403, detail="Domain not allowed")

    if req.action in policy.get("require_confirm_for", []) and not req.confirmed:
        audit({**event_base, "event": "policy_check_failed", "reason": "confirmation_required"})
        raise HTTPException(status_code=412, detail="Confirmation required for this action")

    audit({**event_base, "event": "policy_check_passed", "result": "ok"})

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    task_id = safe_task_id(req.task_id)
    report_name = f"{ts}_{task_id}_summary.md"
    meta_name = f"{ts}_{task_id}_meta.json"

    report_content = (
        f"# Task {task_id}\n\n"
        f"- time: {utc_now_iso()}\n"
        f"- action: {req.action}\n"
        f"- url: {req.url or '-'}\n"
        f"- payload: `{json.dumps(req.payload, ensure_ascii=False)}`\n"
    )

    report_path = HANDOFF_DIR / report_name
    meta_path = HANDOFF_DIR / meta_name

    report_path.write_text(report_content, encoding="utf-8")

    meta = {
        "task_id": task_id,
        "timestamp_utc": utc_now_iso(),
        "action": req.action,
        "url": req.url,
        "payload": req.payload,
        "files": [report_name],
    }
    meta_bytes = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    meta["sha256"] = hashlib.sha256(meta_bytes).hexdigest()
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    audit({**event_base, "event": "artifact_written", "report": report_name, "meta": meta_name})

    return {
        "status": "queued",
        "task_id": task_id,
        "artifacts": {
            "report": str(report_path),
            "meta": str(meta_path),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("phone_agent_prototype:app", host="127.0.0.1", port=8000, reload=False)
