"""Tests for the report-quality matrix and finalization logic."""

import pytest

from app.orchestrator.graph_builder import (
    ReportDisposition,
    ReportQuality,
    _classify_report,
    _sanitize_uncertain_text,
)
from app.services.report_builder import build_single_report


from app.core.state_contract import DiagnosisResult, ImpactAssessment, StrategyDecision


def _make_state(confidence=0.8, verified=True, evidence_score=0.4, history_score=0.3, has_evidence=True):
    """Minimal VulcanOpsState for report builder tests."""
    from app.core.state_contract import VulcanOpsState

    return VulcanOpsState(
        active_machine_id="12345678-1234-5678-1234-567812345678",
        diagnosis=DiagnosisResult(
            root_cause="Bearing wear",
            failure_mode="Rolling element fatigue",
            confidence=confidence,
        ),
        strategy=StrategyDecision(
            recommended_action="Replace bearings",
            parts_required=["bearings"],
        ),
        impact=ImpactAssessment(),
        final_report={
            "evidence_score": evidence_score,
            "history_score": history_score,
            "fallback_used": False,
            "uncertainty_reason": None,
            "final_report_status": "specific",
            "circuit_breaker_state": "CLOSED",
        },
    )


def test_classify_high_confidence_verified():
    disposition, reason = _classify_report(
        confidence=0.9, verified=True, evidence_score=0.4, history_score=0.3, has_evidence=True
    )
    assert disposition == ReportDisposition.SPECIFIC
    assert reason == "high_confidence_verified"


def test_classify_moderate_confidence_with_evidence():
    disposition, reason = _classify_report(
        confidence=0.75, verified=False, evidence_score=0.3, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.SPECIFIC
    assert reason == "moderate_confidence_supported"


def test_classify_low_confidence_partial_evidence():
    disposition, reason = _classify_report(
        confidence=0.55, verified=False, evidence_score=0.3, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.CAUTIOUS
    assert reason == "low_confidence_partial_evidence"


def test_classify_weak_evidence_present():
    disposition, reason = _classify_report(
        confidence=0.45, verified=False, evidence_score=0.15, history_score=0.0, has_evidence=True
    )
    assert disposition == ReportDisposition.CAUTIOUS
    assert reason == "evidence_present_but_weak"


def test_classify_insufficient_evidence():
    disposition, reason = _classify_report(
        confidence=0.45, verified=False, evidence_score=0.0, history_score=0.0, has_evidence=False
    )
    assert disposition == ReportDisposition.FALLBACK
    assert reason == "insufficient_evidence"


def test_sanitize_uncertain_text_strips_certainty_claims():
    raw = "The root cause is confirmed to be bearing failure and is certainly supported by available evidence."
    sanitized = _sanitize_uncertain_text(raw)
    assert "confirmed" not in sanitized.lower()
    assert "certainly is" not in sanitized.lower()
    assert "supported by available evidence" not in sanitized.lower()
    assert "bearing failure" in sanitized.lower()


def test_report_builder_includes_quality_telemetry():
    state = _make_state()
    report = build_single_report(state)
    assert report["evidence_score"] == 0.4
    assert report["history_score"] == 0.3
    assert report["fallback_used"] is False
    assert report["final_report_status"] == "specific"
    assert report["circuit_breaker_state"] == "CLOSED"
