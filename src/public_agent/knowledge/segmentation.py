from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from collections.abc import Sequence

import jieba  # type: ignore[import-untyped]

from public_agent.knowledge.base import TextSegmenter

_SAFE_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_MAX_CUSTOM_TERMS = 10_000
_MAX_CUSTOM_TERM_LENGTH = 100
_INITIALIZATION_LOCK = threading.Lock()


class JiebaChineseSegmenter:
    """Deterministic Chinese search tokenizer with a versioned domain dictionary."""

    def __init__(
        self,
        *,
        custom_terms: Sequence[str] = (),
        max_terms: int = 4_096,
    ) -> None:
        if not 1 <= max_terms <= 20_000:
            raise ValueError("segmenter max_terms must be between 1 and 20000")
        normalized_terms = tuple(
            sorted(
                {
                    unicodedata.normalize("NFKC", term).strip()
                    for term in custom_terms
                    if term.strip()
                }
            )
        )
        if len(normalized_terms) > _MAX_CUSTOM_TERMS:
            raise ValueError("segmenter custom dictionary exceeds 10000 terms")
        if any(len(term) > _MAX_CUSTOM_TERM_LENGTH for term in normalized_terms):
            raise ValueError("segmenter custom terms must be at most 100 characters")

        profile_payload = json.dumps(
            {
                "custom_terms": normalized_terms,
                "hmm": False,
                "max_terms": max_terms,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(profile_payload.encode("utf-8")).hexdigest()[:12]
        self._profile = f"jieba-search-v1:{digest}"
        self._max_terms = max_terms
        with _INITIALIZATION_LOCK:
            previous_level = jieba.default_logger.level
            jieba.default_logger.setLevel(logging.WARNING)
            try:
                tokenizer = jieba.Tokenizer()
                tokenizer.initialize()
                for term in normalized_terms:
                    tokenizer.add_word(term, freq=10_000_000)
            finally:
                jieba.default_logger.setLevel(previous_level)
        self._tokenizer = tokenizer

    @property
    def profile(self) -> str:
        return self._profile

    def segment(self, text: str) -> tuple[str, ...]:
        if not isinstance(text, str):
            raise TypeError("segmenter input must be a string")
        if not text.strip():
            return ()

        tokens: list[str] = []
        seen: set[str] = set()
        normalized_text = unicodedata.normalize("NFKC", text).lower()
        for raw_token in self._tokenizer.cut_for_search(normalized_text, HMM=False):
            for token in _SAFE_TOKEN_PATTERN.findall(raw_token):
                if not token.strip("_") or token in seen:
                    continue
                seen.add(token)
                tokens.append(token)
                if len(tokens) >= self._max_terms:
                    return tuple(tokens)
        return tuple(tokens)


def lexical_text(segmenter: TextSegmenter, text: str) -> str:
    tokens = segmenter.segment(text)
    if not tokens:
        raise ValueError("text does not contain searchable terms")
    return " ".join(tokens)
