import asyncio
import time
import sys

from app.services.llm_service import llm_service

async def run_tests():
    all_passed = True
    
    print("Testing call_json...")
    t0 = time.monotonic()
    try:
        res_json = await llm_service.call_json(
            agent="smoke_test",
            system="You are a helpful assistant. Output JSON.",
            user='Respond with exactly: {"status":"ok","model":"vulcanops"}'
        )
        duration = time.monotonic() - t0
        assert "status" in res_json and "model" in res_json, f"Missing keys: {res_json}"
        assert res_json["status"] == "ok", f"Status not ok: {res_json}"
        print(f"PASS call_json ({duration:.2f}s)")
    except Exception as e:
        duration = time.monotonic() - t0
        print(f"FAIL call_json ({duration:.2f}s): {e}")
        all_passed = False

    print("Testing call_text...")
    t0 = time.monotonic()
    try:
        res_text = await llm_service.call_text(
            agent="smoke_test",
            system="You are a helpful assistant.",
            user="Give me a one-sentence reliability tip."
        )
        duration = time.monotonic() - t0
        assert isinstance(res_text, str) and len(res_text) > 20, f"Invalid text response: {res_text}"
        print(f"PASS call_text ({duration:.2f}s)")
    except Exception as e:
        duration = time.monotonic() - t0
        print(f"FAIL call_text ({duration:.2f}s): {e}")
        all_passed = False

    print("Testing call_with_tools...")
    t0 = time.monotonic()
    try:
        tools = [{
            "type": "function",
            "function": {
                "name": "get_sensor_reading",
                "description": "Get the reading of a sensor",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sensor_id": {"type": "string"}
                    },
                    "required": ["sensor_id"]
                }
            }
        }]
        res_tools = await llm_service.call_with_tools(
            agent="smoke_test",
            system="You are a helpful assistant with access to tools. Use the tool get_sensor_reading.",
            messages=[{"role": "user", "content": "Get the reading for sensor TEMP_01"}],
            tools=tools
        )
        duration = time.monotonic() - t0
        assert res_tools.kind == "tool_call", f"Expected tool_call, got {res_tools.kind}"
        assert res_tools.tool_name == "get_sensor_reading", f"Expected get_sensor_reading, got {res_tools.tool_name}"
        assert getattr(res_tools, 'tool_args', {}).get("sensor_id") == "TEMP_01", f"Expected sensor_id=TEMP_01, got args: {getattr(res_tools, 'tool_args', {})}"
        print(f"PASS call_with_tools ({duration:.2f}s)")
    except Exception as e:
        duration = time.monotonic() - t0
        print(f"FAIL call_with_tools ({duration:.2f}s): {e}")
        all_passed = False

    if not all_passed:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_tests())
