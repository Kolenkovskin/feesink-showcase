"""
FEESINK-IMPORT-SMOKE v2026.01.17-01

Purpose:
- Fail fast on ImportError/SyntaxError after refactors/splits.
- Import core modules:
  - feesink.storage.sqlite
  - feesink.api.server

Hard requirements (FeeSink P0):
- Deterministic banner:
  - script version
  - sha1 of relevant project .py files (stable order)
  - TS_UTC
- Append run output to:
  C:\\Users\\User\\PycharmProjects\\feesink\\logs\\import_smoke.txt
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Tuple

SCRIPT_VERSION = "FEESINK-IMPORT-SMOKE v2026.01.17-01"
LOGS_DIR_WIN = r"C:\Users\User\PycharmProjects\feesink\logs"
LOG_FILE_WIN = os.path.join(LOGS_DIR_WIN, "import_smoke.txt")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_hash_targets(root: Path) -> List[Path]:
    """
    Deterministic allowlist of important modules for import smoke.
    Keep this list stable; add files when new splits happen.
    """
    rels = [
        "scripts/import_smoke.py",
        "feesink/api/server.py",
        "feesink/api/app.py",
        "feesink/api/_http.py",
        "feesink/api/_stripe.py",
        "feesink/api/deps.py",
        "feesink/api/handlers_core.py",
        "feesink/api/handlers_stripe.py",
        "feesink/storage/sqlite.py",
        "feesink/storage/_sqlite_utils.py",
        "feesink/storage/_sqlite_schema.py",
        "feesink/storage/_sqlite_accounts.py",
        "feesink/storage/_sqlite_checks.py",
        "feesink/storage/_sqlite_endpoints.py",
        "feesink/storage/_sqlite_leases.py",
        "feesink/storage/_sqlite_topups.py",
        "feesink/storage/_sqlite_stripe.py",
        "feesink/storage/_sqlite_housekeeping.py",
        "feesink/domain/models.py",
        "feesink/storage/interfaces.py",
    ]
    out: List[Path] = []
    for r in rels:
        p = root / r
        if p.exists():
            out.append(p)
    # stable order
    out.sort(key=lambda x: str(x).lower())
    return out


class _Tee:
    def __init__(self, fp: io.TextIOBase) -> None:
        self._fp = fp

    def write(self, s: str) -> None:
        sys.__stdout__.write(s)
        self._fp.write(s)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._fp.flush()


def _ensure_logs_dir() -> None:
    os.makedirs(LOGS_DIR_WIN, exist_ok=True)


def _print_banner(root: Path) -> None:
    targets = _collect_hash_targets(root)

    print("=" * 80)
    print(SCRIPT_VERSION)
    print("TS_UTC=", _utc_now_iso())
    print("ROOT=", str(root))
    print("HASH_TARGETS=", len(targets))
    print("-" * 80)
    for p in targets:
        rel = str(p.relative_to(root))
        print(f"SHA1 {rel} = {_sha1_file(p)}")
    print("=" * 80)


def _do_imports() -> None:
    # import-only: should raise if broken
    import feesink.storage.sqlite  # noqa: F401
    import feesink.api.server  # noqa: F401


def main() -> int:
    # repo root = parent of "scripts/"
    root = Path(__file__).resolve().parents[1]

    _ensure_logs_dir()
    with open(LOG_FILE_WIN, "a", encoding="utf-8") as f:
        tee = _Tee(f)
        old_stdout = sys.stdout
        sys.stdout = tee
        try:
            _print_banner(root)
            try:
                _do_imports()
            except Exception as e:
                print("IMPORT_SMOKE=FAIL")
                print("ERROR_TYPE=", type(e).__name__)
                print("ERROR=", str(e))
                return 1

            print("IMPORT_SMOKE=PASS")
            return 0
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    raise SystemExit(main())
