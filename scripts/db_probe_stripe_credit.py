# scripts/db_probe_stripe_credit.py
# FEESINK-DB-PROBE-STRIPE-CREDIT v2026.01.05-02

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = "FEESINK-DB-PROBE-STRIPE-CREDIT v2026.01.05-02"


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
        print("\nLATEST provider_event (stripe):")
        ev = con.execute(
            """
            SELECT provider_event_id,
                   event_type,
                   status,
                   received_at_utc,
                   processed_at_utc,
                   account_id,
                   credited_units
            FROM provider_events
            WHERE provider='stripe'
            ORDER BY received_at_utc DESC
            LIMIT 1
            """
        ).fetchone()
        print(dict(ev) if ev else None)

        print("\nLATEST topups:")
        tops = con.execute(
            """
            SELECT tx_hash,
                   account_id,
                   credited_units,
                   amount_usdt,
                   created_at_utc
            FROM topups
            ORDER BY created_at_utc DESC
            LIMIT 5
            """
        ).fetchall()
        print([dict(r) for r in tops])

        print("\nLATEST account (from provider_event.account_id):")
        if ev and ev["account_id"]:
            acc = con.execute(
                """
                SELECT account_id,
                       balance_units,
                       status,
                       created_at_utc,
                       updated_at_utc
                FROM accounts
                WHERE account_id=?
                """,
                (ev["account_id"],),
            ).fetchone()
            print(dict(acc) if acc else None)
        else:
            print("provider_event.account_id is NULL")

    finally:
        con.close()


if __name__ == "__main__":
    main()
