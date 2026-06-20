import asyncio
import sys
import json
import httpx

from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def get_machine_id():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT id FROM machines LIMIT 1"))
        machine = result.fetchone()
        if machine:
            return str(machine[0])
    return None

async def main():
    machine_id = await get_machine_id()
    if not machine_id:
        print("No machine found in DB")
        sys.exit(1)

    print(f"Found machine: {machine_id}")
    
    base_url = "http://127.0.0.1:8000/api/v1"
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("--- Chat ---")
        chat_payload = {"query": "What does high vibration on pump P-101 usually indicate?"}
        try:
            r = await client.post(f"{base_url}/chat", json=chat_payload)
            print(r.status_code)
            print(r.text)
        except Exception as e:
            print("Chat error:", e)

        print("\n--- Deep Analyze ---")
        try:
            r2 = await client.post(f"{base_url}/reports/deep-analyze/{machine_id}")
            print(r2.status_code)
            print(r2.text)
            if r2.status_code == 202:
                job_id = r2.json().get("job_id")
                if job_id:
                    print(f"Polling job {job_id}...")
                    for _ in range(30):
                        r3 = await client.get(f"{base_url}/reports/jobs/{job_id}")
                        job_data = r3.json()
                        status = job_data.get("status")
                        print(f"Status: {status}")
                        if status in ["completed", "complete", "failed"]:
                            print(json.dumps(job_data, indent=2))
                            break
                        await asyncio.sleep(5)
        except Exception as e:
            print("Deep Analyze error:", e)

if __name__ == "__main__":
    asyncio.run(main())
