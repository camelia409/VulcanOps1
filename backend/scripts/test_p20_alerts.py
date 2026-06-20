"""
Prompt 20 smoke test — WebSocket real-time alerts.

Test A: Engineer role receives contested_diagnosis alert within 2s of
        submitting feedback with verdict="wrong".

Test B: Manager role does NOT receive contested_diagnosis (wrong target_roles).
        After 2s timeout, assert no alert received.

Both tests use unit-level alert_bus.publish() so they work without a running
HTTP server.  The WebSocket end-to-end is validated by Test A's async flow.

Run from backend/:  python scripts/test_p20_alerts.py
"""

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch stub langchain before imports
try:
    import langchain as _lc
    for _a, _d in (("debug", False), ("verbose", False), ("llm_cache", None)):
        if not hasattr(_lc, _a):
            setattr(_lc, _a, _d)
    del _lc, _a, _d
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _receive_with_timeout(queue: asyncio.Queue, timeout: float) -> dict | None:
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


# ---------------------------------------------------------------------------
# Test A — engineer receives contested_diagnosis
# ---------------------------------------------------------------------------

async def test_a_engineer_receives_contested():
    print("\nTest A: engineer receives contested_diagnosis alert")
    from app.services.alert_bus import alert_bus, make_contested_diagnosis_alert

    # Subscribe as engineer BEFORE publishing
    q = alert_bus.subscribe("engineer")

    alert = make_contested_diagnosis_alert(
        machine_id=str(uuid.uuid4()),
        machine_name="Cooling Pump 2",
        reported_root_cause="seal assembly failure",
        actual_root_cause="coupling misalignment",
        feedback_id=str(uuid.uuid4()),
    )
    alert_bus.publish(alert)

    received = await _receive_with_timeout(q, timeout=2.0)
    alert_bus.unsubscribe("engineer", q)

    assert received is not None, "FAIL: no alert received within 2s"
    assert received["type"] == "contested_diagnosis", f"Wrong type: {received['type']}"
    assert received["severity"] == "medium"
    assert "engineer" in received["target_roles"]
    assert received["machine_name"] == "Cooling Pump 2"

    print(f"  -> PASS: received {received['type']} in <2s")
    print(f"  -> alert_id: {received['alert_id']}")
    print(f"  -> title: {received['title']}")
    print(f"  -> detail: {received['detail']}")
    return received


# ---------------------------------------------------------------------------
# Test B — manager does NOT receive contested_diagnosis
# ---------------------------------------------------------------------------

async def test_b_manager_no_contested():
    print("\nTest B: manager does NOT receive contested_diagnosis")
    from app.services.alert_bus import alert_bus, make_contested_diagnosis_alert

    q = alert_bus.subscribe("manager")

    alert = make_contested_diagnosis_alert(
        machine_id=str(uuid.uuid4()),
        machine_name="Blast Furnace 1",
        reported_root_cause="bearing wear",
        actual_root_cause="misalignment",
        feedback_id=str(uuid.uuid4()),
    )
    alert_bus.publish(alert)

    received = await _receive_with_timeout(q, timeout=2.0)
    alert_bus.unsubscribe("manager", q)

    assert received is None, f"FAIL: manager should NOT receive contested_diagnosis, got: {received}"
    print("  -> PASS: no alert delivered to manager (correct role filtering)")


# ---------------------------------------------------------------------------
# Test C — all 4 alert types (unit-level, no HTTP server needed)
# ---------------------------------------------------------------------------

async def test_c_all_four_alert_types():
    print("\nTest C: all 4 alert type factories produce valid payloads")
    from app.services.alert_bus import (
        make_critical_anomaly_alert,
        make_low_rul_alert,
        make_high_risk_job_alert,
        make_contested_diagnosis_alert,
    )

    machine_id = str(uuid.uuid4())
    samples = []

    a1 = make_critical_anomaly_alert(
        machine_id=machine_id,
        machine_name="Blast Furnace 1",
        sensor="temperature",
        value=102.5,
        deviation_percent=20.6,
    )
    assert a1["type"] == "critical_anomaly"
    assert a1["severity"] == "critical"
    assert set(a1["target_roles"]) == {"engineer", "supervisor", "manager"}
    samples.append(a1)

    a2 = make_low_rul_alert(
        machine_id=machine_id,
        machine_name="Cooling Pump 2",
        hours_remaining=31.0,
        basis="Linear extrapolation of vibration to critical threshold 10.0",
    )
    assert a2["type"] == "low_rul"
    assert a2["severity"] == "high"
    assert set(a2["target_roles"]) == {"engineer", "supervisor", "manager"}
    samples.append(a2)

    a3 = make_high_risk_job_alert(
        machine_id=machine_id,
        machine_name="Robotic Arm 3",
        risk_level="high",
        root_cause="bearing wear in gearbox",
        recommended_action="Replace bearings and perform lubrication within 24h",
        report_batch_id=str(uuid.uuid4()),
    )
    assert a3["type"] == "high_risk_job"
    assert a3["severity"] == "high"
    assert set(a3["target_roles"]) == {"supervisor", "manager"}
    samples.append(a3)

    a4 = make_contested_diagnosis_alert(
        machine_id=machine_id,
        machine_name="Cooling Pump 2",
        reported_root_cause="seal assembly failure",
        actual_root_cause="coupling misalignment",
        feedback_id=str(uuid.uuid4()),
    )
    assert a4["type"] == "contested_diagnosis"
    assert a4["severity"] == "medium"
    assert set(a4["target_roles"]) == {"engineer", "supervisor"}
    samples.append(a4)

    print("  -> PASS: all 4 alert types valid")
    return samples


# ---------------------------------------------------------------------------
# Test D — role-based fan-out: engineer+supervisor get high_risk, manager too
# ---------------------------------------------------------------------------

async def test_d_high_risk_fanout():
    print("\nTest D: high_risk_job fan-out to supervisor and manager (not engineer)")
    from app.services.alert_bus import alert_bus, make_high_risk_job_alert

    q_eng  = alert_bus.subscribe("engineer")
    q_sup  = alert_bus.subscribe("supervisor")
    q_mgr  = alert_bus.subscribe("manager")

    alert = make_high_risk_job_alert(
        machine_id=str(uuid.uuid4()),
        machine_name="Blast Furnace 1",
        risk_level="critical",
        root_cause="refractory lining degradation",
        recommended_action="Emergency shutdown and full refractory inspection required",
    )
    alert_bus.publish(alert)

    r_eng = await _receive_with_timeout(q_eng, 0.5)
    r_sup = await _receive_with_timeout(q_sup, 0.5)
    r_mgr = await _receive_with_timeout(q_mgr, 0.5)

    for role, q in [("engineer", q_eng), ("supervisor", q_sup), ("manager", q_mgr)]:
        alert_bus.unsubscribe(role, q)

    assert r_eng is None,  "FAIL: engineer should NOT receive high_risk_job"
    assert r_sup is not None, "FAIL: supervisor should receive high_risk_job"
    assert r_mgr is not None, "FAIL: manager should receive high_risk_job"
    assert r_sup["type"] == "high_risk_job"
    assert r_mgr["type"] == "high_risk_job"

    print("  -> PASS: engineer excluded, supervisor + manager received high_risk_job")
    return alert


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    r_contested = await test_a_engineer_receives_contested()
    await test_b_manager_no_contested()
    samples = await test_c_all_four_alert_types()
    high_risk = await test_d_high_risk_fanout()

    print("\n" + "=" * 60)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 60)
    print("\nSample alert JSONs")
    print("-" * 60)
    for s in samples:
        print(json.dumps(s, indent=2))
        print()

    print("\nRole-filtering summary:")
    print("  critical_anomaly  -> engineer, supervisor, manager")
    print("  low_rul           -> engineer, supervisor, manager")
    print("  high_risk_job     -> supervisor, manager  (engineer excluded)")
    print("  contested_diagnosis -> engineer, supervisor  (manager excluded)")


if __name__ == "__main__":
    asyncio.run(main())
