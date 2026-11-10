"""Gateway Prompt Cache with Exact and Semantic hashing and cost tracking (MATRIX Gateway Layer 2).

Features:
- Deterministic SHA-256 hash key of (model, messages, temperature)
- Semantic prompt normalization key for robust matching
- Per-entry TTL and expiration pruning
- Real-time metrics: hits, misses, tokens saved, USD cost saved
- Integration with MATRIX pricing engine
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from matrix_server.pricing import compute_cost


def _canonicalize_messages(messages: list[dict[str, Any]] | list[Any]) -> list[dict[str, str]]:
    """Converts messages to canonical list of {role, content} dicts."""
    canonical: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, dict):
            role = str(m.get("role", "")).strip().lower()
            content = str(m.get("content", ""))
        else:
            role = getattr(m, "role", "").strip().lower()
            content = str(getattr(m, "content", ""))
        canonical.append({"role": role, "content": content})
    return canonical


def _normalize_text(text: str) -> str:
    """Normalizes whitespace and casing for semantic prompt lookup."""
    return re.sub(r"\s+", " ", text).strip().lower()


def compute_cache_key(
    model: str,
    messages: list[dict[str, Any]] | list[Any],
    temperature: float,
    semantic: bool = False,
) -> str:
    """Computes a SHA-256 hash key from (model, messages, temperature)."""
    norm_model = model.strip().lower()
    canonical_msgs = _canonicalize_messages(messages)

    if semantic:
        processed_msgs = [
            {"role": m["role"], "content": _normalize_text(m["content"])}
            for m in canonical_msgs
        ]
        # Semantic matching groups near-zero temperatures (e.g. deterministic queries)
        rounded_temp = round(float(temperature), 1)
    else:
        processed_msgs = canonical_msgs
        rounded_temp = round(float(temperature), 4)

    payload = {
        "model": norm_model,
        "messages": processed_msgs,
        "temperature": rounded_temp,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@dataclass
class CacheEntry:
    key: str
    semantic_key: str
    response: dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    created_at: float
    expires_at: float


@dataclass
class GatewayCache:
    default_ttl_seconds: int = 3600
    max_entries: int = 10_000
    enable_semantic_cache: bool = True

    _entries: dict[str, CacheEntry] = field(default_factory=dict)
    _semantic_index: dict[str, str] = field(default_factory=dict)  # semantic_key -> primary_key
    _lock: threading.Lock = field(default_factory=threading.Lock)

    _hits: int = 0
    _misses: int = 0
    _tokens_saved: int = 0
    _cost_saved_usd: float = 0.0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def tokens_saved(self) -> int:
        return self._tokens_saved

    @property
    def cost_saved_usd(self) -> float:
        return round(self._cost_saved_usd, 6)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return round(self._hits / total, 4) if total > 0 else 0.0

    def get(
        self,
        model: str,
        messages: list[dict[str, Any]] | list[Any],
        temperature: float = 0.7,
    ) -> dict[str, Any] | None:
        """Looks up a cached response by exact SHA-256 hash or semantic prompt hash."""
        now = time.monotonic()
        exact_key = compute_cache_key(model, messages, temperature, semantic=False)

        with self._lock:
            # 1. Try exact match
            entry = self._entries.get(exact_key)

            # 2. If no exact match and semantic caching enabled, try semantic key
            if entry is None and self.enable_semantic_cache:
                sem_key = compute_cache_key(model, messages, temperature, semantic=True)
                mapped_key = self._semantic_index.get(sem_key)
                if mapped_key:
                    entry = self._entries.get(mapped_key)

            if entry is not None:
                if entry.expires_at > now:
                    # Cache Hit!
                    self._hits += 1
                    self._tokens_saved += entry.total_tokens
                    self._cost_saved_usd += entry.cost_usd
                    return copy.deepcopy(entry.response)
                else:
                    # Expired, evict
                    self._evict_entry(entry.key)

            # Cache Miss
            self._misses += 1
            return None

    def set(
        self,
        model: str,
        messages: list[dict[str, Any]] | list[Any],
        temperature: float,
        response: dict[str, Any],
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        """Stores a completion response in the cache and computes saved cost metrics."""
        now = time.monotonic()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = now + ttl

        # Extract tokens from response usage if not directly provided
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        p_tokens = (
            prompt_tokens
            if prompt_tokens is not None
            else usage.get("prompt_tokens", 0)
        )
        c_tokens = (
            completion_tokens
            if completion_tokens is not None
            else usage.get("completion_tokens", 0)
        )
        tot_tokens = usage.get("total_tokens", p_tokens + c_tokens)

        cost_usd = compute_cost(model, input_tokens=p_tokens, output_tokens=c_tokens)

        exact_key = compute_cache_key(model, messages, temperature, semantic=False)
        sem_key = compute_cache_key(model, messages, temperature, semantic=True)

        with self._lock:
            if len(self._entries) >= self.max_entries:
                self._prune_lru_or_expired(now)

            entry = CacheEntry(
                key=exact_key,
                semantic_key=sem_key,
                response=copy.deepcopy(response),
                model=model,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=tot_tokens,
                cost_usd=cost_usd,
                created_at=now,
                expires_at=expires_at,
            )

            self._entries[exact_key] = entry
            if self.enable_semantic_cache:
                self._semantic_index[sem_key] = exact_key

        return exact_key

    def _evict_entry(self, key: str) -> None:
        """Evicts an entry by exact key (caller must hold _lock)."""
        entry = self._entries.pop(key, None)
        if entry and entry.semantic_key in self._semantic_index:
            self._semantic_index.pop(entry.semantic_key, None)

    def _prune_lru_or_expired(self, now: float) -> None:
        """Evicts expired entries or oldest entry if full."""
        expired = [k for k, v in self._entries.items() if v.expires_at <= now]
        for k in expired:
            self._evict_entry(k)

        if len(self._entries) >= self.max_entries:
            # Evict earliest created
            oldest_key = min(self._entries.keys(), key=lambda k: self._entries[k].created_at)
            self._evict_entry(oldest_key)

    def clear(self) -> None:
        """Clears all cached entries and semantic indexes."""
        with self._lock:
            self._entries.clear()
            self._semantic_index.clear()

    def reset_stats(self) -> None:
        """Resets telemetry metrics."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._tokens_saved = 0
            self._cost_saved_usd = 0.0

    def stats(self) -> dict[str, Any]:
        """Returns cache telemetry summary."""
        with self._lock:
            total = self._hits + self._misses
            rate = round(self._hits / total, 4) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total,
                "hit_rate": rate,
                "tokens_saved": self._tokens_saved,
                "cost_saved_usd": round(self._cost_saved_usd, 6),
                "cached_entries": len(self._entries),
            }
