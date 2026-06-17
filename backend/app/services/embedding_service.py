"""Embedding service — wraps fastembed for local, API-free vector generation.

Model: BAAI/bge-small-en-v1.5
  - 384 dimensions
  - ~130 MB ONNX model, cached in ~/.cache/fastembed after first load
  - English-optimised, strong on industrial/technical text
  - No API key, no network call after first download

Thread safety: fastembed's TextEmbedding is not async-native, so we run it
in a thread executor to avoid blocking the event loop.

Graceful degradation: if the model fails to load (e.g. first-run download
timeout on a slow connection), embed() returns None and callers must fall
back to keyword search.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_embedder: Any = None   # TextEmbedding instance, created on first use
_load_failed = False    # once failed, stop retrying in this process


def _get_embedder():
    """Return the fastembed TextEmbedding instance, creating it on first call."""
    global _embedder, _load_failed
    if _load_failed:
        return None
    if _embedder is None:
        try:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(model_name=_MODEL_NAME)
            logger.info("Embedding model %s loaded", _MODEL_NAME)
        except Exception as exc:
            _load_failed = True
            logger.warning("Embedding model failed to load (%s) — keyword fallback active", exc)
    return _embedder


def _embed_sync(text: str) -> list[float] | None:
    """Synchronous embedding call — must run in a thread executor."""
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        vectors = list(embedder.embed([text]))
        return vectors[0].tolist()
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return None


def _embed_batch_sync(texts: list[str]) -> list[list[float] | None]:
    """Embed a list of texts in one pass — more efficient than one-at-a-time."""
    embedder = _get_embedder()
    if embedder is None:
        return [None] * len(texts)
    try:
        vectors = list(embedder.embed(texts))
        return [v.tolist() for v in vectors]
    except Exception as exc:
        logger.warning("Batch embedding failed: %s", exc)
        return [None] * len(texts)


async def embed(text: str) -> list[float] | None:
    """Async wrapper — runs embedding in a thread to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_sync, text)


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Async batch embedding — single thread executor call for the whole batch."""
    if not texts:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_batch_sync, texts)
