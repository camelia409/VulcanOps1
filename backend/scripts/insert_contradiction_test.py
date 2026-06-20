"""Inserts 3 contradictory maintenance records for Cooling Pump 2 to force cycle."""
import asyncio, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PUMP2_ID = "bd9c66b3-ad3c-2d6d-1a3d-1fa7bc8960a9"

SQL_INSERT = """
INSERT INTO maintenance_records
  (maintenance_id, machine_id, date, failure_mode, action_taken, downtime_hours, engineer)
VALUES
  (gen_random_uuid(), :mid, CURRENT_DATE - 3,
   'Seal Leakage',
   'Replaced thrust bearing; seal was intact on inspection. Misdiagnosed as seal failure initially.',
   6, 'verification-test'),
  (gen_random_uuid(), :mid, CURRENT_DATE - 6,
   'Seal Leakage',
   'Realigned coupling; seal replacement was unnecessary. Different root cause than expected.',
   4, 'verification-test'),
  (gen_random_uuid(), :mid, CURRENT_DATE - 9,
   'Seal Leakage',
   'Recalibrated vibration sensor; no actual seal issue. Turned out to be calibration drift not seal wear.',
   2, 'verification-test')
"""

SQL_CHECK = """
SELECT failure_mode, action_taken
FROM maintenance_records
WHERE engineer='verification-test'
"""

SQL_DELETE = "DELETE FROM maintenance_records WHERE engineer='verification-test'"


async def insert():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        await db.execute(text(SQL_INSERT), {"mid": PUMP2_ID})
        await db.commit()
        r = await db.execute(text(SQL_CHECK))
        rows = r.fetchall()
        print(f"Inserted {len(rows)} records:")
        for row in rows:
            print(" ", row)


async def delete():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(SQL_DELETE))
        await db.commit()
        print(f"Deleted {r.rowcount} test records.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "insert"
    if mode == "delete":
        asyncio.run(delete())
    else:
        asyncio.run(insert())
