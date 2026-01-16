# FeeSink module size guard
# FEESINK-LINT-MODULE-SIZE v2026.01.16-01
#
# Policy:
# - Default max lines per .py: 700
# - Allowlist exceptions for transitional baseline ONLY (explicit, minimal).
# - Deterministic output; non-zero exit on violation.
#
# Run:
#   python .\scripts\lint_module_size.py
#
# Notes:
# - Counts physical lines in UTF-8 text.
# - Skips venv/.venv, __pycache__, .git, build artifacts.
# - Only checks files under repo root (feesink/, scripts/ by default).
#
# IMPORTANT:
# - Allowlist must be reduced/removed as soon as files are split under 700.
# - This is a controlled migration aid, not a permanent bypass.

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-LINT-MODULE-SIZE v2026.01.16-01"
UTC = timezone.utc

DEFAULT_MAX_LINES = 700

# relative paths from repo root
DEFAULT_INCLUDE_DIRS = ("feesink", "scripts")

# Transitional allowlist (baseline)
# Exact limits set to current measured line counts so the rule stays meaningful.
# If a file grows beyond this, the hook will fail.
ALLOWLIST_OVER_700: dict[str, int] = {
    "feesink/api/server.py": 1491,
    "feesink/storage/sqlite.py": 1040,
}

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

SKIP_FILE_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class Violation:
    rel_path: str
    lines: int
    limit: int


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    # scripts/.. = repo root
    return Path(__file__).resolve().parents[1]


def _should_skip_dir(path: Path) -> bool:
    parts = set(path.parts)
    return any(x in parts for x in SKIP_DIR_NAMES)


def _count_lines(p: Path) -> int:
    data = p.read_text(encoding="utf-8", errors="strict")
    return len(data.splitlines())


def _iter_py_files(root: Path, include_dirs: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for d in include_dirs:
        base = root / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if p.suffix in SKIP_FILE_SUFFIXES:
                continue
            if _should_skip_dir(p.parent):
                continue
            out.append(p)
    out.sort(key=lambda x: x.as_posix().lower())
    return out


def main() -> int:
    root = _repo_root()

    max_lines = int(os.getenv("FEESINK_MAX_MODULE_LINES", str(DEFAULT_MAX_LINES)).strip() or DEFAULT_MAX_LINES)
    include_dirs_raw = os.getenv("FEESINK_LINT_INCLUDE_DIRS", ",".join(DEFAULT_INCLUDE_DIRS))
    include_dirs = tuple([x.strip() for x in include_dirs_raw.split(",") if x.strip()]) or DEFAULT_INCLUDE_DIRS

    print("=" * 80)
    print(VERSION)
    print("TS_UTC=", _utc_now())
    print("ROOT=", str(root))
    print("MAX_LINES=", max_lines)
    print("INCLUDE_DIRS=", ",".join(include_dirs))
    print("ALLOWLIST_COUNT=", len(ALLOWLIST_OVER_700))
    print("=" * 80)

    violations: list[Violation] = []

    files = _iter_py_files(root, include_dirs)

    checked = 0
    for p in files:
        rel = p.relative_to(root).as_posix()
        lines = _count_lines(p)
        checked += 1

        if rel in ALLOWLIST_OVER_700:
            allowed = int(ALLOWLIST_OVER_700[rel])
            if lines > allowed:
                violations.append(Violation(rel_path=rel, lines=lines, limit=allowed))
            continue

        if lines > max_lines:
            violations.append(Violation(rel_path=rel, lines=lines, limit=max_lines))

    print(f"CHECKED_FILES={checked}")
    print(f"VIOLATIONS={len(violations)}")

    if violations:
        print("-" * 80)
        print("VIOLATION_LIST (rel_path | lines | limit):")
        for v in violations:
            print(f"- {v.rel_path} | {v.lines} | {v.limit}")
        print("-" * 80)
        print("FAIL: module size policy violated.")
        return 2

    print("PASS: module size policy satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
