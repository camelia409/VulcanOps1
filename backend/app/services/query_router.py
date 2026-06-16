"""
Query Router — deterministic keyword-based intent classifier.

Converts a free-text user query into a structured routing decision.
No LLM. No embeddings. Pure keyword matching with confidence scoring.

Supported intents:
    plant_summary           — "Plant overview", "How is the plant?"
    highest_risk            — "Which machine is at highest risk?"
    top_priority            — "Show top 3 priority machines"
    emergency_machines      — "Show emergency machines"
    low_confidence_machines — "Which machines have low confidence diagnoses?"
    rul_query               — "What is its RUL?" / "Remaining useful life for …"
    investigate_machine     — "Investigate compressor anomalies"
    prioritize_today        — "Prioritize maintenance today"
    critical_machines       — "Show critical machines"
    daily_report            — "Generate today's reliability report"
"""

import re
from dataclasses import dataclass, field


# ── intent keyword sets ───────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "plant_summary": [
        ("plant overview",      1.0),
        ("plant summary",       1.0),
        ("plant health",        0.95),
        ("overall status",      0.9),
        ("how is the plant",    0.9),
        ("how is everything",   0.85),
        ("overview",            0.75),
        ("summary",             0.7),
        ("plant status",        0.9),
        ("fleet status",        0.85),
    ],
    "highest_risk": [
        ("which machine is at highest risk", 1.0),
        ("highest risk",                     1.0),
        ("most at risk",                     1.0),
        ("most critical machine",            0.95),
        ("highest priority machine",         0.9),
        ("worst machine",                    0.85),
        ("most dangerous",                   0.85),
        ("which machine",                    0.6),
        ("at risk",                          0.65),
    ],
    "top_priority": [
        ("top priority",          1.0),
        ("top 3 priority",        1.0),
        ("top 3",                 0.9),
        ("most urgent",           0.95),
        ("highest priority",      0.9),
        ("priority machines",     0.85),
        ("what needs attention",  0.8),
        ("what needs fixing",     0.8),
    ],
    "emergency_machines": [
        ("emergency machines",   1.0),
        ("show emergency",       1.0),
        ("list emergency",       1.0),
        ("emergency",            0.9),
        ("immediate attention",  0.95),
        ("immediate action",     0.95),
        ("critical alert",       0.85),
    ],
    "low_confidence_machines": [
        ("low confidence",          1.0),
        ("uncertain diagnosis",     0.95),
        ("manual inspection",       0.9),
        ("insufficient evidence",   0.9),
        ("uncertain",               0.75),
        ("unsure",                  0.75),
        ("needs inspection",        0.8),
        ("unverified",              0.75),
    ],
    "rul_query": [
        ("remaining useful life", 1.0),
        ("when will it fail",     0.95),
        ("time to failure",       0.9),
        ("life remaining",        0.85),
        ("how long until",        0.8),
        ("rul for",               0.85),
        ("rul is",                0.8),
        ("rul",                   0.75),
        ("how long",              0.6),
    ],
    "investigate_machine": [
        ("investigate",   0.85),
        ("diagnose",      0.85),
        ("analyse",       0.8),
        ("analyze",       0.8),
        ("what's wrong",  0.8),
        ("whats wrong",   0.8),
        ("check",         0.65),
        ("inspect",       0.7),
        ("anomaly",       0.75),
        ("anomalies",     0.75),
        ("fault",         0.7),
        ("failure",       0.7),
        ("tell me about", 0.7),
        ("show me",       0.6),
        ("details",       0.6),
    ],
    "prioritize_today": [
        ("prioritize maintenance today", 1.0),
        ("prioritize today",             1.0),
        ("what needs maintenance today", 1.0),
        ("maintenance priority",         0.85),
        ("schedule today",               0.85),
        ("prioritize",                   0.7),
        ("what to fix today",            0.8),
        ("today's maintenance",          0.8),
    ],
    "critical_machines": [
        ("show critical machines",    1.0),
        ("list critical machines",    1.0),
        ("critical machines",         0.95),
        ("critical equipment",        0.9),
        ("critical assets",           0.9),
        ("which are critical",        0.9),
        ("show critical",             0.85),
        ("list critical",             0.85),
    ],
    "daily_report": [
        ("generate today's reliability report", 1.0),
        ("reliability report",                  0.95),
        ("daily report",                        0.95),
        ("today's report",                      0.9),
        ("generate report",                     0.85),
        ("report for today",                    0.85),
        ("morning report",                      0.8),
        ("shift report",                        0.75),
    ],
}

# ── reference words — signals that user means the last-discussed machine ─────

_REFERENCE_PATTERNS: list[str] = [
    "its rul", "its risk", "its priority", "its status", "its confidence",
    " its ", " it ", "that machine", "the machine", "the same machine",
    "for it", "about it", "on it", "about that", "for that",
]

# ── machine type keywords for target extraction ───────────────────────────────

_MACHINE_TYPES: list[str] = [
    "compressor", "pump", "motor", "turbine", "conveyor",
    "boiler", "generator", "fan", "valve", "blower",
    "chiller", "heat exchanger", "gearbox", "agitator",
    "hydraulic", "cooling tower", "air handler", "furnace", "reactor",
]


@dataclass
class RoutingResult:
    intent: str
    target_machine: str | None
    confidence: float
    has_reference: bool = field(default=False)


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


def _score_intent(query_norm: str, phrases: list[tuple[str, float]]) -> float:
    best = 0.0
    for phrase, weight in phrases:
        if phrase in query_norm:
            best = max(best, weight)
    return best


def _extract_machine_type(query_norm: str) -> str | None:
    for machine_type in _MACHINE_TYPES:
        if machine_type in query_norm:
            return machine_type
    return None


def _detect_reference(query_norm: str) -> bool:
    padded = f" {query_norm} "
    return any(p in padded for p in _REFERENCE_PATTERNS)


def route(query: str) -> RoutingResult:
    """
    Map a user query to an intent, with optional machine type extraction
    and session-memory reference detection.

    Returns:
        RoutingResult with intent, optional target_machine, confidence,
        and has_reference flag (True when query references a prior machine
        via pronouns or "that machine").
        Falls back to 'plant_summary' with confidence 0.4 when no intent matches.
    """
    q = _normalise(query)

    scores: dict[str, float] = {
        intent: _score_intent(q, phrases)
        for intent, phrases in _INTENT_KEYWORDS.items()
    }

    best_intent = max(scores, key=lambda k: scores[k])
    best_score = scores[best_intent]

    has_reference = _detect_reference(q)
    target_machine = _extract_machine_type(q)

    # Direct machine-name / RUL / low-confidence queries → investigate_machine
    if target_machine or "rul" in q or "low confidence" in q:
        scores["investigate_machine"] = max(scores.get("investigate_machine", 0.0), 0.9)
        best_intent = max(scores, key=lambda k: scores[k])
        best_score = scores[best_intent]

    # RUL keywords → prefer rul_query when not also investigate
    if "rul" in q and not target_machine:
        scores["rul_query"] = max(scores.get("rul_query", 0.0), 0.9)
        best_intent = max(scores, key=lambda k: scores[k])
        best_score = scores[best_intent]

    # Reference with no strong intent → investigate the referenced machine
    if has_reference and best_score < 0.7:
        scores["investigate_machine"] = max(scores.get("investigate_machine", 0.0), 0.85)
        best_intent = "investigate_machine"
        best_score = scores[best_intent]

    # No match → default to plant_summary (more useful than prioritize_today as default)
    if best_score == 0.0:
        return RoutingResult(
            intent="plant_summary",
            target_machine=None,
            confidence=0.4,
            has_reference=has_reference,
        )

    target_machine_final = (
        target_machine
        if best_intent in ("investigate_machine", "rul_query")
        else None
    )

    return RoutingResult(
        intent=best_intent,
        target_machine=target_machine_final,
        confidence=round(best_score, 2),
        has_reference=has_reference,
    )
