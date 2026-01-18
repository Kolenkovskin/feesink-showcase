"""
FeeSink — run_smoke_all.py

Runs mandatory local smoke sequence:
1) import_smoke.py
2) db_smoke_sqlite.py
3) run_demo_tick_twice.py

Hard rules:
- stop on first FAIL (non-zero exit)
- write an append-only log to logs/run_smoke_all.txt
- print deterministic header + SUMMARY line

Version:
- FEESINK-RUN-SMOKE-ALL v2026.01.18-01
"""

from __future__ import annotations

import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


VERSION = "FEESINK-RUN-SMOKE-ALL v2026.01.18-01"


def _ts_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root_from_this_file() -> Path:
    # scripts/run_smoke_all.py -> repo root = parent of scripts/
    return Path(__file__).resolve().parent.parent


def _logs_dir(root: Path) -> Path:
    return root / "logs"


def _append_log(log_file: Path, text: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _run_step(
    *,
    root: Path,
    log_file: Path,
    label: str,
    rel_script: str,
    env: dict,
) -> int:
    script_path = root / rel_script
    if not script_path.exists():
        msg = f"[FATAL] missing script: {rel_script}"
        print(msg)
        _append_log(log_file, msg)
        return 2

    cmd = [sys.executable, str(script_path)]
    banner = (
        "--------------------------------------------------------------------------------\n"
        f"[STEP] {label}\n"
        f"TS_UTC= {_ts_utc()}\n"
        f"CMD= {' '.join(cmd)}\n"
        "--------------------------------------------------------------------------------"
    )
    print(banner)
    _append_log(log_file, banner)

    p = subprocess.run(
        cmd,
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    out = p.stdout or ""
    print(out, end="" if out.endswith("\n") else "\n")
    _append_log(log_file, out)

    rc_line = f"[STEP_END] {label} exit_code={p.returncode}"
    print(rc_line)
    _append_log(log_file, rc_line)

    return int(p.returncode)


def main() -> int:
    root = _repo_root_from_this_file()
    log_file = _logs_dir(root) / "run_smoke_all.txt"

    header = (
        "================================================================================\n"
        f"{VERSION}\n"
        f"TS_UTC= {_ts_utc()}\n"
        f"ROOT= {root}\n"
        f"PY= {sys.executable}\n"
        f"LOG_FILE= {log_file}\n"
        "================================================================================"
    )
    print(header)
    _append_log(log_file, header)

    # inherit current env; keep it deterministic (no extra mutations)
    env = dict(os.environ)

    steps: List[Tuple[str, str]] = [
        ("IMPORT_SMOKE", r"scripts\import_smoke.py"),
        ("DB_SMOKE_SQLITE", r"scripts\db_smoke_sqlite.py"),
        ("DEMO_TICK_TWICE", r"scripts\run_demo_tick_twice.py"),
    ]

    results = {}
    for label, rel_script in steps:
        rc = _run_step(root=root, log_file=log_file, label=label, rel_script=rel_script, env=env)
        results[label] = "PASS" if rc == 0 else f"FAIL({rc})"
        if rc != 0:
            summary = (
                "================================================================================\n"
                f"SUMMARY: import={results.get('IMPORT_SMOKE','SKIP')} "
                f"sqlite={results.get('DB_SMOKE_SQLITE','SKIP')} "
                f"demo_tick={results.get('DEMO_TICK_TWICE','SKIP')}\n"
                f"RESULT=FAIL (stopped on {label})\n"
                "================================================================================"
            )
            print(summary)
            _append_log(log_file, summary)
            return rc

    summary = (
        "================================================================================\n"
        f"SUMMARY: import={results['IMPORT_SMOKE']} "
        f"sqlite={results['DB_SMOKE_SQLITE']} "
        f"demo_tick={results['DEMO_TICK_TWICE']}\n"
        "RESULT=PASS\n"
        "================================================================================"
    )
    print(summary)
    _append_log(log_file, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
