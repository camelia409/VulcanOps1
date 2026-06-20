"""Check strategy fields from latest batch."""
import asyncio, sys, json, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BATCH_ID = "9b9a6d73-658f-4bde-9e2a-93c01d8bd495"


async def main():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text("SELECT full_report_json FROM report_batches WHERE batch_id=:b"),
            {"b": BATCH_ID},
        )
        d = r.scalar_one()
        if not isinstance(d, dict):
            d = json.loads(d)
        # Print top-level keys
        print("Top-level keys:", list(d.keys()))
        # Check recommended_action, priority, procurement, constraint fields
        for field in ["recommended_action", "priority", "parts_required",
                      "procurement_strategy", "constraint_violations",
                      "procurement_gap"]:
            val = d.get(field)
            if val is not None:
                v = str(val)[:120]
                print(f"  {field}: {v}")


asyncio.run(main())
