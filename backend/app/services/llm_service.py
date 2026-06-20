"""
LLM service — sole LLM provider call point for VulcanOps.

Uses an OpenAI-compatible endpoint (configurable via LLM_BASE_URL).
Provides three public methods:
  - call_json:       structured JSON output
  - call_with_tools: native tool-calling
  - call_text:       plain text completion

Every call emits a structured log entry with status, timing, and token usage.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

_ModelT = TypeVar("_ModelT", bound=BaseModel)

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")


class LLMError(Exception):
    """Base class for all LLM service failures."""


class LLMTimeout(LLMError):
    """The LLM request exceeded the configured timeout."""


class LLMEmpty(LLMError):
    """The LLM returned an empty or unusable response."""


class LLMJSONError(LLMError):
    """The LLM returned text that could not be parsed as JSON."""


class LLMAPIError(LLMError):
    """The LLM provider returned an HTTP error or connection failure."""


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """Result of a native tool-calling LLM call."""

    kind: str  # "tool_call" | "final"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    content: str | None = None
    tool_call_id: str | None = None


# Fallback payloads returned to callers when the LLM is unavailable.
_DIAGNOSIS_FALLBACK: dict[str, Any] = {
    "root_cause": "manual inspection required",
    "failure_mode": "insufficient evidence",
    "reasoning": "LLM service unavailable. Analysis based on deterministic sensor thresholds only.",
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


def _telemetry_from_usage(usage: Any) -> dict[str, int | None]:
    if usage is None:
        return {"tokens_in": None, "tokens_out": None}
    return {
        "tokens_in": getattr(usage, "prompt_tokens", None),
        "tokens_out": getattr(usage, "completion_tokens", None),
    }


def _log_llm_call(
    *,
    agent: str,
    status: str,
    duration_ms: float,
    error: str | None = None,
    tool_calls: int = 0,
    usage: Any = None,
) -> None:
    """Emit a single structured log line for every LLM invocation."""
    telemetry = _telemetry_from_usage(usage)
    payload = {
        "event": "llm_call",
        "agent": agent,
        "model": settings.LLM_MODEL,
        "provider": "ollama",
        "duration_ms": round(duration_ms, 1),
        "status": status,
        "tokens_in": telemetry["tokens_in"],
        "tokens_out": telemetry["tokens_out"],
        "tool_calls": tool_calls,
    }
    if error:
        payload["error"] = error
    _PIPELINE_LOG.info(json.dumps(payload))


class LLMService:
    """
    Single reusable LLM gateway for VulcanOps.

    Configured via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL (OpenAI-compatible).
    Timeout: settings.LLM_TIMEOUT_SECONDS (default 30 s). Retries: 0.
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not settings.LLM_API_KEY:
                raise LLMError("LLM_API_KEY is not configured")
            self._client = AsyncOpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=0,
            )
        return self._client

    async def call_json(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Call the model with JSON output format. Returns the parsed dict.

        Raises LLMTimeout, LLMEmpty, LLMJSONError, or LLMAPIError.
        """
        t0 = time.monotonic()
        try:
            response = await self._get_client().chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=timeout,
            )
        except APITimeoutError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="timeout",
                duration_ms=duration_ms,
                error=f"timeout: {exc}",
            )
            raise LLMTimeout(f"LLM request timed out: {exc}") from exc
        except (APIConnectionError, APIStatusError) as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"api_error: {exc}",
            )
            raise LLMAPIError(f"LLM API error: {exc}") from exc
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"unexpected: {exc}",
            )
            raise LLMAPIError(f"Unexpected LLM error: {exc}") from exc

        duration_ms = (time.monotonic() - t0) * 1000
        message = response.choices[0].message if response.choices else None
        content = message.content if message else None

        if not content:
            _log_llm_call(
                agent=agent,
                status="empty",
                duration_ms=duration_ms,
                usage=response.usage,
            )
            raise LLMEmpty("LLM returned empty content")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            _log_llm_call(
                agent=agent,
                status="json_error",
                duration_ms=duration_ms,
                error=f"json_error: {exc}",
                usage=response.usage,
            )
            raise LLMJSONError(f"LLM returned invalid JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            _log_llm_call(
                agent=agent,
                status="json_error",
                duration_ms=duration_ms,
                error="top-level JSON is not an object",
                usage=response.usage,
            )
            raise LLMJSONError("LLM returned non-object JSON")

        _log_llm_call(
            agent=agent,
            status="success",
            duration_ms=duration_ms,
            usage=response.usage,
        )
        return parsed

    async def call_with_tools(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: float | None = None,
    ) -> ToolCallResult:
        """
        Native tool-calling. Returns either a tool_call or a final text response.

        Raises LLMTimeout, LLMEmpty, or LLMAPIError.
        """
        t0 = time.monotonic()
        full_messages = [{"role": "system", "content": system}, *messages]

        try:
            response = await self._get_client().chat.completions.create(
                model=settings.LLM_MODEL,
                messages=full_messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
                timeout=timeout,
            )
        except APITimeoutError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="timeout",
                duration_ms=duration_ms,
                error=f"timeout: {exc}",
            )
            raise LLMTimeout(f"LLM request timed out: {exc}") from exc
        except (APIConnectionError, APIStatusError) as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"api_error: {exc}",
            )
            raise LLMAPIError(f"LLM API error: {exc}") from exc
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"unexpected: {exc}",
            )
            raise LLMAPIError(f"Unexpected LLM error: {exc}") from exc

        duration_ms = (time.monotonic() - t0) * 1000
        message = response.choices[0].message if response.choices else None
        if message is None:
            _log_llm_call(
                agent=agent,
                status="empty",
                duration_ms=duration_ms,
                usage=response.usage,
            )
            raise LLMEmpty("LLM returned no choices")

        content = message.content or ""
        raw_tool_calls = getattr(message, "tool_calls", None) or []

        if not raw_tool_calls:
            _log_llm_call(
                agent=agent,
                status="success",
                duration_ms=duration_ms,
                usage=response.usage,
            )
            return ToolCallResult(
                kind="final",
                content=content,
                tool_call_id=None,
            )

        first = raw_tool_calls[0]
        tool_call_id = getattr(first, "id", None) or f" synthetic-{uuid.uuid4().hex[:8]}"
        tool_name = getattr(first.function, "name", None)
        arguments = getattr(first.function, "arguments", "{}")

        try:
            tool_args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            # Tool arguments are supposed to be valid JSON by OpenAI spec.
            # If the LLM violates this, treat it as an API-level error.
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"tool_args_not_json: {exc}",
                usage=response.usage,
                tool_calls=len(raw_tool_calls),
            )
            raise LLMAPIError(f"Tool call arguments were not valid JSON: {exc}") from exc

        _log_llm_call(
            agent=agent,
            status="success",
            duration_ms=duration_ms,
            usage=response.usage,
            tool_calls=len(raw_tool_calls),
        )
        return ToolCallResult(
            kind="tool_call",
            tool_name=tool_name,
            tool_args=tool_args,
            content=content,
            tool_call_id=tool_call_id,
        )

    async def call_text(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        timeout: float | None = None,
    ) -> str:
        """Plain text completion. Returns the assistant's content string."""
        t0 = time.monotonic()
        try:
            response = await self._get_client().chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                timeout=timeout,
            )
        except APITimeoutError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="timeout",
                duration_ms=duration_ms,
                error=f"timeout: {exc}",
            )
            raise LLMTimeout(f"LLM request timed out: {exc}") from exc
        except (APIConnectionError, APIStatusError) as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"api_error: {exc}",
            )
            raise LLMAPIError(f"LLM API error: {exc}") from exc
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_llm_call(
                agent=agent,
                status="api_error",
                duration_ms=duration_ms,
                error=f"unexpected: {exc}",
            )
            raise LLMAPIError(f"Unexpected LLM error: {exc}") from exc

        duration_ms = (time.monotonic() - t0) * 1000
        message = response.choices[0].message if response.choices else None
        content = message.content if message else None

        if not content:
            _log_llm_call(
                agent=agent,
                status="empty",
                duration_ms=duration_ms,
                usage=response.usage,
            )
            raise LLMEmpty("LLM returned empty content")

        _log_llm_call(
            agent=agent,
            status="success",
            duration_ms=duration_ms,
            usage=response.usage,
        )
        return content

    # ── convenience wrappers for legacy callers ─────────────────────────────────

    async def generate_role_reports(self, prompt: str) -> dict[str, Any]:
        """Legacy wrapper: JSON role reports (engineer/supervisor/manager)."""
        system = (
            "You are a senior reliability engineer writing operational reports for an industrial plant. "
            "Respond ONLY with valid JSON. No prose, no markdown fences, no explanation outside the JSON object.\n"
            "Each summary must be 150-200 words, specific, factual, and grounded in the evidence provided.\n"
            'Required schema: {"engineer":"<string>","supervisor":"<string>","manager":"<string>"}\n\n'
            "REPORT RULES:\n"
            "- Use concrete component/system names and sensor values from the investigation summary.\n"
            "- Cite evidence briefly (e.g., 'vibration 12% above threshold', 'manual states inspect seal every 2000h').\n"
            "- Do NOT use generic filler such as 'further investigation is needed' unless the summary explicitly states low confidence or manual inspection.\n"
            "- Do NOT invent failures, costs, or timelines not present in the data.\n"
            "- engineer: field engineer performing the repair — fault description, first checks, parts, safety, post-repair monitoring.\n"
            "- supervisor: shift supervisor coordinating the response — operational impact, resource needs, timeline, escalation.\n"
            "- manager: plant management — business risk, cost exposure, compliance, strategic recommendation."
        )
        try:
            raw = await self.call_json(
                agent="communication_agent",
                system=system,
                user=prompt,
            )
        except LLMError:
            return {**_COMMUNICATION_FALLBACK, "_telemetry": {"fallback_used": True}}

        return {
            "engineer": _to_str(raw.get("engineer"), _COMMUNICATION_FALLBACK["engineer"]),
            "supervisor": _to_str(raw.get("supervisor"), _COMMUNICATION_FALLBACK["supervisor"]),
            "manager": _to_str(raw.get("manager"), _COMMUNICATION_FALLBACK["manager"]),
            "_telemetry": {"fallback_used": False},
        }

    async def call_structured(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        schema: type[_ModelT],
        timeout: float | None = None,
    ) -> _ModelT:
        """call_json + Pydantic model_validate for type-safe structured output.

        Works with any OpenAI-compatible endpoint (including Ollama) without
        requiring the beta.chat.completions.parse API.
        Raises LLMError (or subclass) on LLM failure, ValidationError on schema mismatch.
        """
        raw = await self.call_json(agent=agent, system=system, user=user, timeout=timeout)
        return schema.model_validate(raw)

    async def generate_copilot_answer(self, machine_facts: str, question: str) -> str:
        """Legacy wrapper: short grounded answer for the chat copilot."""
        system = (
            "You are an industrial reliability copilot. "
            "Answer the operator's question using ONLY the machine data provided. "
            "Be concise: 1-2 sentences. Plain English. No bullet points. "
            "Never invent failures, root causes, or sensor values not present in the data. "
            "If the answer is not in the data, say so directly.\n"
            'Required schema: {"answer": "<string>"}'
        )
        prompt = f"Machine data:\n{machine_facts}\n\nOperator question: {question}"
        try:
            raw = await self.call_json(
                agent="copilot",
                system=system,
                user=prompt,
            )
        except LLMError:
            return ""
        return _to_str(raw.get("answer"), "")


def _to_str(value: Any, fallback: str = "") -> str:
    """Coerce an LLM field value to a plain string."""
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "report", "summary", "content", "message", "body", "narrative"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        parts = [str(v) for v in value.values() if v is not None and str(v).strip()]
        return " ".join(parts) if parts else fallback
    return str(value)


# Module-level singleton — one AsyncOpenAI client per process
llm_service = LLMService()
