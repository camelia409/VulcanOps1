"""Check cycle run result from DB."""
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
        print("recommendation:", d.get("verification_recommendation"))
        print("revision_count:", d.get("verification_revision_count"))
        print("contradictions:", json.dumps(d.get("verification_contradictions", []), indent=2))
        trace = d.get("execution_trace", [])
        print("trace:")
        for t in trace:
            print(f"  {t.get('agent')} ({t.get('status')}, {t.get('duration_ms')}ms)")


asyncio.run(main())
