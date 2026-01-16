# scripts/db_probe_stripe_event_payload.py
# FEESINK-DB-PROBE-STRIPE-EVENT-PAYLOAD v2026.01.05-01

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-DB-PROBE-STRIPE-EVENT-PAYLOAD v2026.01.05-01"


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    print("=" * 80)
    print(VERSION)
    print("START_UTC=", utc())
    print("=" * 80)

    db = os.getenv("FEESINK_SQLITE_DB", "feesink.db")
    print("DB_PATH=", db)

    if not Path(db).exists():
        print("ERROR: DB file not found")
        return 2

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute(
            """
            SELECT provider_event_id, raw_event_json
            FROM provider_events
            WHERE provider='stripe'
            ORDER BY received_at_utc DESC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            print("No stripe provider_events found")
            return 0

        print("\nprovider_event_id:", row["provider_event_id"])

        ev = json.loads(row["raw_event_json"])
        print("event.type:", ev.get("type"))

        sess = ((ev.get("data") or {}).get("object") or {})

        print("session.id:", sess.get("id"))
        print("payment_status:", sess.get("payment_status"))
        print("metadata:", sess.get("metadata"))
        print("customer:", sess.get("customer"))
        print("has_line_items:", "line_items" in sess)

    finally:
        con.close()


if __name__ == "__main__":
    main()
