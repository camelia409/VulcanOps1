"""
Evidence Verification Agent — deterministic cross-check of diagnosis against evidence.

No LLM. Pure keyword and pattern matching.

Input  : state.diagnosis, state.retrieved_evidence, state.maintenance_history
Output : AgentResult.data = {
    "verified": bool,
    "evidence_score": float,    # 0.0 – 1.0: fraction of diagnosis keywords found in evidence
    "history_score": float,     # 0.0 – 1.0: alignment with historical failure modes
    "warnings": list[str],
    "verification_notes": str
}
"""

import re

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that",
    "this", "for", "with", "on", "at", "by", "be", "as", "are", "was",
}

_VERIFICATION_THRESHOLD = 0.25  # minimum evidence_score to be "verified"


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def run(state: VulcanOpsState) -> AgentResult:
    warnings: list[str] = []

    if not state.diagnosis:
        return AgentResult(
            status="error",
            data={},
            errors=["No diagnosis available to verify"],
        )

    diagnosis = state.diagnosis

    if not diagnosis.root_cause:
        return AgentResult(
            status="error",
            data={},
            errors=["Diagnosis has no root_cause — cannot verify"],
        )

    # Build diagnosis keyword set from root_cause + failure_mode
    diagnosis_text = " ".join(filter(None, [diagnosis.root_cause, diagnosis.failure_mode]))
    diagnosis_keywords = _tokenize(diagnosis_text)

    if not diagnosis_keywords:
        return AgentResult(
            status="error",
            data={},
            errors=["Could not extract meaningful keywords from diagnosis"],
        )

    # ── Evidence score: keyword overlap with retrieved documentary evidence ──
    evidence_score = 0.0
    if state.retrieved_evidence:
        evidence_corpus = " ".join(
            ev.get("chunk", "") for ev in state.retrieved_evidence
        )
        evidence_keywords = _tokenize(evidence_corpus)
        matched = diagnosis_keywords & evidence_keywords
        evidence_score = round(len(matched) / len(diagnosis_keywords), 4)

        if evidence_score < _VERIFICATION_THRESHOLD:
            warnings.append(
                f"Low evidence support: only {len(matched)}/{len(diagnosis_keywords)} "
                "diagnosis keywords appear in retrieved documents"
            )
    else:
        warnings.append("No retrieved evidence available — evidence score is 0.0")

    # ── History score: alignment with historical failure modes ──
    history_score = 0.0
    if state.maintenance_history:
        history_text = " ".join(r.failure_mode for r in state.maintenance_history)
        history_keywords = _tokenize(history_text)
        matched_history = diagnosis_keywords & history_keywords
        history_score = round(len(matched_history) / len(diagnosis_keywords), 4)

        if history_score < _VERIFICATION_THRESHOLD:
            warnings.append(
                "Diagnosis does not strongly align with historical failure modes "
                f"({len(matched_history)}/{len(diagnosis_keywords)} keyword match)"
            )
    else:
        warnings.append("No maintenance history available — history score is 0.0")

    # ── Confidence check ──
    if diagnosis.confidence is not None and diagnosis.confidence < 0.5:
        warnings.append(
            f"LLM diagnosis confidence is low ({diagnosis.confidence:.2f}). "
            "Treat findings as preliminary."
        )

    # Verified if evidence score meets threshold OR history corroborates.
    # Uncertainty correction is applied by the global override in graph_builder
    # _finalize_node — this agent reports raw keyword-match results only.
    verified = evidence_score >= _VERIFICATION_THRESHOLD or history_score >= _VERIFICATION_THRESHOLD

    combined_score = round((evidence_score * 0.6 + history_score * 0.4), 4)
    notes = (
        f"Evidence score {evidence_score:.2f}, history alignment {history_score:.2f}. "
        + ("Diagnosis is supported by available evidence." if verified else
           "Diagnosis could not be corroborated — escalate for manual inspection.")
    )

    return AgentResult(
        status="success",
        data={
            "verified": verified,
            "evidence_score": evidence_score,
            "history_score": history_score,
            "combined_score": combined_score,
            "warnings": warnings,
            "verification_notes": notes,
        },
    )
