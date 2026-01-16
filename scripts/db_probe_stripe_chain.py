# scripts/db_probe_stripe_chain.py
# FEESINK-DB-PROBE-STRIPE-CHAIN v2026.01.05-01

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

VERSION = "FEESINK-DB-PROBE-STRIPE-CHAIN v2026.01.05-01"


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

    env_price = (os.getenv("STRIPE_PRICE_ID_EUR_50") or "").strip() or None
    print("ENV STRIPE_PRICE_ID_EUR_50=", env_price)

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute(
            """
            SELECT provider_event_id, raw_event_json, received_at_utc
            FROM provider_events
            WHERE provider='stripe'
            ORDER BY received_at_utc DESC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            print("No stripe provider_events found")
            return 0

        provider_event_id = row["provider_event_id"]
        ev = json.loads(row["raw_event_json"])
        event_type = ev.get("type")
        sess = ((ev.get("data") or {}).get("object") or {})
        session_id = sess.get("id")
        payment_status = sess.get("payment_status")
        metadata = sess.get("metadata") if isinstance(sess.get("metadata"), dict) else {}

        meta_account = metadata.get("account_id")
        meta_price = metadata.get("price_id")

        print("\nLATEST provider_event:")
        print("  provider_event_id=", provider_event_id)
        print("  event.type=", event_type)
        print("  session.id=", session_id)
        print("  payment_status=", payment_status)
        print("  metadata.account_id=", meta_account)
        print("  metadata.price_id=", meta_price)

        if not session_id:
            print("\nFAIL: session.id missing in raw_event_json")
            return 0

        # 1) stripe_links lookup
        link = con.execute(
            """
            SELECT stripe_session_id, account_id, stripe_customer_id, created_at_utc
            FROM stripe_links
            WHERE stripe_session_id=?
            """,
            (session_id,),
        ).fetchone()

        print("\nSTRIPE_LINKS lookup by session.id:")
        print("  found=", bool(link))
        print("  row=", dict(link) if link else None)

        # 2) price mapping check
        price_match = (env_price is not None) and (meta_price == env_price)
        print("\nPRICE MAPPING:")
        print("  meta_price_id=", meta_price)
        print("  env_price_id =", env_price)
        print("  match=", price_match)

        # 3) topup lookup by canonical tx_hash scheme
        tx_hash = f"stripe:{provider_event_id}"
        top = con.execute(
            """
            SELECT tx_hash, account_id, credited_units, amount_usdt, created_at_utc
            FROM topups
            WHERE tx_hash=?
            """,
            (tx_hash,),
        ).fetchone()

        print("\nTOPUP lookup by tx_hash:")
        print("  tx_hash=", tx_hash)
        print("  found=", bool(top))
        print("  row=", dict(top) if top else None)

        # 4) account balance (prefer topup.account_id, else link.account_id, else meta_account)
        acc_id = None
        if top:
            acc_id = top["account_id"]
        elif link:
            acc_id = link["account_id"]
        elif isinstance(meta_account, str) and meta_account.strip():
            acc_id = meta_account.strip()

        print("\nACCOUNT lookup:")
        print("  resolved_account_id=", acc_id)

        if acc_id:
            acc = con.execute(
                """
                SELECT account_id, balance_units, status, updated_at_utc
                FROM accounts
                WHERE account_id=?
                """,
                (acc_id,),
            ).fetchone()
            print("  row=", dict(acc) if acc else None)
        else:
            print("  row=None (no account_id resolved)")

        # Deterministic diagnosis line:
        print("\nDIAG:")
        if not link:
            print("  FAIL: stripe_links missing session.id => resolve_account_by_stripe_session cannot work.")
        elif not price_match:
            print("  FAIL: metadata.price_id != ENV STRIPE_PRICE_ID_EUR_50 => credited_units mapping fails.")
        elif not top:
            print("  FAIL: mapping+link look OK, but topup missing => credit_topup not executed or failed.")
        else:
            print("  OK: topup exists => credit executed (balance should be >0).")

    finally:
        con.close()


if __name__ == "__main__":
    main()
