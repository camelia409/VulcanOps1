"""Check prior_feedback_considered and root_cause from latest batch."""
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
        print("root_cause:", d.get("root_cause"))
        print("failure_mode:", d.get("failure_mode"))
        print("prior_feedback_considered:", json.dumps(d.get("prior_feedback_considered", []), indent=2))


asyncio.run(main())
