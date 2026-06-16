"""
LLM service — sole OpenRouter call point for VulcanOps.

Only diagnosis_agent and communication_agent may import this module.
No other file in the codebase should call OpenRouter directly.
"""

import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from app.core.config import settings
from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

logger = logging.getLogger(__name__)

# Structured pipeline logger — emits JSON lines for per-call observability.
# Grep for "vulcanops.pipeline" in logs to trace every LLM invocation.
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

# ── in-process LLM prompt cache ───────────────────────────────────────────────
# Key  : md5(model + "||" + system_prompt + "||" + user_prompt)
# Value: (result_dict, telemetry_dict)
# When a deep-analysis button is clicked minutes after ingestion and sensor
# data has not changed, the prompts are identical → cache hit, zero latency.
# Process-scoped; cleared on server restart. Max 500 entries (LRU eviction).

_LLM_CACHE: OrderedDict[str, tuple[dict[str, Any], dict[str, Any]]] = OrderedDict()
_LLM_CACHE_MAX = 500


def _prompt_cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    raw = f"{model}||{system_prompt}||{user_prompt}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


def llm_cache_stats() -> dict[str, int]:
    """Return current cache size (useful for health checks / debugging)."""
    return {"entries": len(_LLM_CACHE), "max": _LLM_CACHE_MAX}

# ── fallbacks returned on any API failure ──────────────────────────────────────

_DIAGNOSIS_FALLBACK: dict[str, Any] = {
    "root_cause": "manual inspection required",
    "failure_mode": "insufficient evidence",
    "reasoning": "LLM unavailable — circuit breaker open. Analysis based on deterministic sensor thresholds only.",
    "confidence": 0.2,
    "evidence_used": [],
}

_CIRCUIT_BREAKER_COMMUNICATION_MESSAGE = (
    "Evidence is insufficient to determine root cause. "
    "Perform manual inspection before repair actions."
)

_COMMUNICATION_FALLBACK: dict[str, Any] = {
    "engineer": _CIRCUIT_BREAKER_COMMUNICATION_MESSAGE,
    "supervisor": _CIRCUIT_BREAKER_COMMUNICATION_MESSAGE,
    "manager": _CIRCUIT_BREAKER_COMMUNICATION_MESSAGE,
}

_FALLBACK_TELEMETRY: dict[str, Any] = {
    "model": "fallback",
    "latency_ms": 0.0,
    "input_tokens": 0,
    "output_tokens": 0,
    "fallback_used": True,
}

# ── circuit breaker ────────────────────────────────────────────────────────────
# One breaker per process. Wraps the actual OpenRouter network call inside
# llm_service._call(). On OPEN it short-circuits the request and returns the
# deterministic fallback without hitting the API.

llm_circuit_breaker = CircuitBreaker()

# ── system prompts ─────────────────────────────────────────────────────────────

_JSON_RULES = (
    "\n\nCRITICAL INSTRUCTIONS:\n"
    "Return ONLY valid JSON.\n"
    "Do NOT explain.\n"
    "Do NOT reason.\n"
    "Do NOT use markdown.\n"
    "Do NOT use code blocks.\n"
    "Do NOT use comments.\n"
    "Do NOT prefix with //\n"
    "Do NOT suffix with text.\n"
    "Do NOT output analysis.\n"
    "Do NOT output chain of thought.\n"
    "Output exactly one JSON object."
)

_DIAGNOSIS_SYSTEM = (
    "You are an industrial reliability engineer performing root cause analysis. "
    "Respond ONLY with valid JSON. No prose, no markdown fences, no explanation outside the JSON object.\n"
    'Required schema: {"root_cause":"<string>","failure_mode":"<string>",'
    '"reasoning":"<string>","confidence":<float 0.0-1.0>,"evidence_used":["<string>",...]}\n\n'
    "EVIDENCE GROUNDING RULES — read carefully before responding:\n"
    "You may ONLY use evidence explicitly present in this request: sensor readings, "
    "maintenance history, manuals, and SOPs provided below.\n"
    "CRITICAL: Never invent failures. Never infer from general mechanical knowledge. "
    "Never guess a component failure that is not directly supported by the provided data.\n\n"
    "CONFIDENCE SCALE:\n"
    "- 0.70–0.89: Cautious diagnosis — evidence suggests a likely cause but is not definitive. "
    "Use precise industrial language and note what is uncertain.\n"
    "- 0.50–0.69: Preliminary diagnosis — some evidence points to a possible cause, but the signal is mixed. "
    "State the most likely cause and the gaps that need confirmation.\n"
    "- < 0.50: Insufficient evidence — set root_cause='manual inspection required', "
    "failure_mode='insufficient evidence', and explain what data is missing.\n\n"
    "When evidence is moderate or stronger, produce a concrete, actionable diagnosis with specific component or system names found in the data. "
    "Do NOT default to generic fallback text when the evidence supports a real diagnosis."
    + _JSON_RULES
)

_COPILOT_SYSTEM = (
    "You are an industrial reliability copilot. "
    "Answer the operator's question using ONLY the machine data provided. "
    "Be concise: 1-2 sentences. Plain English. No bullet points. "
    "Never invent failures, root causes, or sensor values not present in the data. "
    "If the answer is not in the data, say so directly.\n"
    'Required schema: {"answer": "<string>"}'
    + _JSON_RULES
)

_COMMUNICATION_SYSTEM = (
    "You are a senior reliability engineer writing operational reports for an industrial plant. "
    "Respond ONLY with valid JSON. No prose, no markdown fences, no explanation outside the JSON object.\n"
    "Each summary must be 150-200 words, specific, factual, and grounded in the evidence provided.\n"
    'Required schema: {"engineer":"<string>","supervisor":"<string>","manager":"<string>"}\n\n'
    "REPORT RULES:\n"
    "- Use concrete component/system names and sensor values from the investigation summary.\n"
    "- Cite evidence briefly (e.g., 'vibration 12% above threshold', 'maintenance history shows seal replacement').\n"
    "- Do NOT use generic filler such as 'further investigation is needed' unless the summary explicitly states low confidence.\n"
    "- Do NOT invent failures, costs, or timelines not present in the data.\n"
    "- engineer: fault description, first checks, parts, safety, post-repair monitoring.\n"
    "- supervisor: operational impact, resource needs, timeline, escalation.\n"
    "- manager: business risk, cost exposure, compliance, strategic recommendation."
    + _JSON_RULES
)


# ── JSON extraction helpers ────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """Remove Qwen3 chain-of-thought <think>...</think> blocks before JSON."""
    return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()


def _extract_json(text: str) -> dict[str, Any]:
    """
    Robustly parse JSON from an LLM response.
    Handles: raw JSON, markdown code blocks, JSON embedded in prose.
    Raises ValueError if no valid JSON can be found.
    """
    text = _strip_thinking(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON in LLM response (first 200 chars): {text[:200]!r}")


# ── coercion helpers ──────────────────────────────────────────────────────────

def _to_str(value: Any, fallback: str = "") -> str:
    """Coerce an LLM field value to a plain string.

    Gemini (and other models) sometimes return a nested dict instead of a plain
    string for fields declared as "<string>" in the schema — e.g.
      {"engineer": {"summary": "...", "actions": [...]}}
    This helper normalises all such cases so callers always receive str.
    """
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Try common prose-field names the model may have invented
        for key in ("text", "report", "summary", "content", "message", "body", "narrative"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        # No canonical key — join all non-empty string leaf values in order
        parts = [str(v) for v in value.values() if v is not None and str(v).strip()]
        return " ".join(parts) if parts else fallback
    return str(value)


# ── service ────────────────────────────────────────────────────────────────────

class OpenRouterLLMService:
    """
    Single reusable LLM gateway for VulcanOps.
    Uses AsyncOpenAI configured for OpenRouter's API.

    Timeout: settings.LLM_TIMEOUT (default 45 s).  Retries: 0.  On any failure: deterministic fallback.
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
                # Hard total-request timeout. httpx.Timeout(n) was a per-chunk
                # read timeout and did not bound total latency. A plain float
                # is interpreted by the openai SDK as the total request timeout.
                timeout=settings.LLM_TIMEOUT,
                # No retries: a single slow/failed call already occupies the
                # request slot. Retrying compounds latency without recovery benefit.
                max_retries=0,
                default_headers={
                    "HTTP-Referer": "https://vulcanops.io",
                    "X-Title": "VulcanOps",
                },
            )
        return self._client

    async def _do_api_call(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> dict[str, Any]:
        """
        Raw OpenRouter call. Raises on any failure so the circuit breaker can
        count it. The caller (_call) handles fallback and telemetry.
        """
        if not settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY not set")

        response = await self._get_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )

        raw_text = response.choices[0].message.content or ""
        parsed = _extract_json(raw_text)

        # Enforce dict contract: json.loads() can return str/list/int when the
        # model doubly-encodes the JSON object or returns non-object JSON.
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception as exc:
                raise ValueError(f"LLM returned non-object JSON string: {exc}")
        if not isinstance(parsed, dict):
            raise ValueError(f"LLM returned non-dict JSON: {type(parsed).__name__}")

        return parsed

    async def _call(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        temperature: float = 0.1,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Internal call wrapper.
        Returns (result_dict, telemetry_dict).
        On failure returns (fallback, fallback_telemetry).

        Cache behaviour
        ───────────────
        Before calling the API, checks the in-process prompt cache.
        Cache key = md5(model + system_prompt + user_prompt).
        A hit returns the stored (result, telemetry) with cache_hit=True
        and zero additional latency. A miss calls the API through the circuit
        breaker and stores the result for future hits.
        """
        if not settings.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY not set — returning fallback")
            return fallback, {**_FALLBACK_TELEMETRY, "error": "API key not configured", "cache_hit": False}

        # ── cache check ───────────────────────────────────────────────────────
        cache_key = _prompt_cache_key(model, system_prompt, user_prompt)
        if cache_key in _LLM_CACHE:
            _LLM_CACHE.move_to_end(cache_key)  # LRU: mark as recently used
            cached_result, cached_telem = _LLM_CACHE[cache_key]
            hit_telem = {**cached_telem, "cache_hit": True, "latency_ms": 0.0}
            _PIPELINE_LOG.info(json.dumps({
                "event": "llm_cache_hit",
                "model": model,
                "cache_key": cache_key[:8],
            }))
            return cached_result, hit_telem

        t0 = time.monotonic()
        try:
            parsed = await llm_circuit_breaker.execute(
                self._do_api_call,
                model,
                system_prompt,
                user_prompt,
                temperature,
            )
        except CircuitBreakerOpen:
            _PIPELINE_LOG.warning(json.dumps({
                "event": "llm_circuit_breaker_open",
                "model": model,
                "cache_hit": False,
            }))
            return fallback, {
                **_FALLBACK_TELEMETRY,
                "error": "Circuit breaker OPEN",
                "circuit_breaker": "OPEN",
                "cache_hit": False,
            }
        except Exception as exc:
            logger.warning("OpenRouter error: %s", exc)
            _PIPELINE_LOG.warning(json.dumps({
                "event": "llm_error",
                "error": str(exc),
                "model": model,
            }))
            return fallback, {**_FALLBACK_TELEMETRY, "error": str(exc), "cache_hit": False}

        latency_ms = (time.monotonic() - t0) * 1000

        telemetry: dict[str, Any] = {
            "model": model,
            "latency_ms": round(latency_ms, 1),
            "input_tokens": 0,
            "output_tokens": 0,
            "fallback_used": False,
            "cache_hit": False,
            "circuit_breaker": "CLOSED",
        }

        # ── store in cache ────────────────────────────────────────────────────
        if len(_LLM_CACHE) >= _LLM_CACHE_MAX:
            _LLM_CACHE.popitem(last=False)  # evict oldest entry
        _LLM_CACHE[cache_key] = (parsed, telemetry)

        # ── structured log ────────────────────────────────────────────────────
        _PIPELINE_LOG.info(json.dumps({
            "event": "llm_call",
            "model": model,
            "latency_ms": telemetry["latency_ms"],
            "input_tokens": telemetry["input_tokens"],
            "output_tokens": telemetry["output_tokens"],
            "cache_hit": False,
            "cache_key": cache_key[:8],
        }))

        return parsed, telemetry

    async def generate_diagnosis(self, prompt: str) -> dict[str, Any]:
        """
        Call settings.LLM_MODEL and return structured root cause analysis.

        Returns dict with keys:
            root_cause, failure_mode, reasoning, confidence, evidence_used, _telemetry

        Always returns a complete dict — falls back to deterministic values on failure.
        """
        raw, telemetry = await self._call(
            model=settings.LLM_MODEL,
            system_prompt=_DIAGNOSIS_SYSTEM,
            user_prompt=prompt,
            fallback=dict(_DIAGNOSIS_FALLBACK),
            temperature=0.1,
        )

        confidence = raw.get("confidence", _DIAGNOSIS_FALLBACK["confidence"])
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = _DIAGNOSIS_FALLBACK["confidence"]
        confidence = max(0.0, min(1.0, confidence))

        root_cause   = raw.get("root_cause")   or _DIAGNOSIS_FALLBACK["root_cause"]
        failure_mode = raw.get("failure_mode") or _DIAGNOSIS_FALLBACK["failure_mode"]
        reasoning    = raw.get("reasoning")    or _DIAGNOSIS_FALLBACK["reasoning"]

        # Preserve the LLM's diagnosis. A downstream uncertainty guard in
        # graph_builder._finalize_node decides whether to collapse to fallback
        # text based on confidence, verification, and evidence availability.
        # This service only enforces sane bounds and valid JSON shape.
        if confidence < 0.5 and root_cause != "manual inspection required":
            logger.info(
                "Diagnosis confidence %.2f is low but not forcing fallback; "
                "downstream finalizer will decide (was: %r)",
                confidence,
                root_cause,
            )

        return {
            "root_cause":    root_cause,
            "failure_mode":  failure_mode,
            "reasoning":     reasoning,
            "confidence":    confidence,
            "evidence_used": raw.get("evidence_used") or [],
            "_telemetry":    telemetry,
        }

    async def generate_role_reports(self, prompt: str) -> dict[str, Any]:
        """
        Call settings.LLM_MODEL and return three role-specific summaries.

        Returns dict with keys:
            engineer, supervisor, manager, _telemetry

        Always returns a complete dict — falls back to deterministic values on failure.
        """
        raw, telemetry = await self._call(
            model=settings.LLM_MODEL,
            system_prompt=_COMMUNICATION_SYSTEM,
            user_prompt=prompt,
            fallback=dict(_COMMUNICATION_FALLBACK),
            temperature=0.2,
        )

        return {
            "engineer":   _to_str(raw.get("engineer"),   _COMMUNICATION_FALLBACK["engineer"]),
            "supervisor": _to_str(raw.get("supervisor"), _COMMUNICATION_FALLBACK["supervisor"]),
            "manager":    _to_str(raw.get("manager"),    _COMMUNICATION_FALLBACK["manager"]),
            "_telemetry": telemetry,
        }


    async def generate_copilot_answer(self, machine_facts: str, question: str) -> str:
        """
        Generate a short (1-2 sentence) copilot answer for a specific operator question.
        Input is structured machine data; output is a grounded plain-English sentence.
        Falls back to empty string on failure (caller handles gracefully).
        """
        prompt = f"Machine data:\n{machine_facts}\n\nOperator question: {question}"
        raw, _ = await self._call(
            model=settings.LLM_MODEL,
            system_prompt=_COPILOT_SYSTEM,
            user_prompt=prompt,
            fallback={"answer": ""},
            temperature=0.1,
        )
        return _to_str(raw.get("answer"), "")


# Module-level singleton — one AsyncOpenAI client per process
llm_service = OpenRouterLLMService()
