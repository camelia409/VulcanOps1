import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("DELETE FROM engineer_feedback WHERE engineer_id='test-engineer-1'"))
        await db.commit()
        print(f"Deleted {r.rowcount} test feedback rows.")

asyncio.run(main())
