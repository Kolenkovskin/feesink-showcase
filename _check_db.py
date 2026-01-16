import sqlite3

DB = r"C:\Users\User\PycharmProjects\feesink\feesink.db"

con = sqlite3.connect(DB)

print("provider_events:")
rows = con.execute(
    "SELECT provider, provider_event_id, received_at_utc "
    "FROM provider_events "
    "WHERE provider='stripe' "
    "ORDER BY received_at_utc DESC "
    "LIMIT 3"
).fetchall()
print(rows)

print("\naccounts:")
rows = con.execute(
    "SELECT account_id, balance_units, updated_at_utc "
    "FROM accounts "
    "WHERE account_id='demo-user'"
).fetchall()
print(rows)

con.close()
