from __future__ import annotations

from public_agent.knowledge.base import KnowledgeChunkDraft


class TextChunker:
    def __init__(self, *, max_chars: int = 1200, overlap_chars: int = 200) -> None:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be non-negative and smaller than max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def chunk(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[KnowledgeChunkDraft, ...]:
        if max_chunks is not None and max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            raise ValueError("knowledge document content must not be blank")

        chunks: list[KnowledgeChunkDraft] = []
        start = 0
        while start < len(normalized):
            hard_end = min(start + self._max_chars, len(normalized))
            end = self._preferred_boundary(normalized, start, hard_end)
            segment = normalized[start:end]
            content = segment.strip()
            if content:
                if max_chunks is not None and len(chunks) >= max_chunks:
                    raise ValueError(
                        f"knowledge document exceeds the {max_chunks} chunk ingestion limit"
                    )
                left_trim = len(segment) - len(segment.lstrip())
                right_trim = len(segment) - len(segment.rstrip())
                chunks.append(
                    KnowledgeChunkDraft(
                        chunk_index=len(chunks),
                        content=content,
                        start_char=start + left_trim,
                        end_char=end - right_trim,
                    )
                )
            if end >= len(normalized):
                break
            start = max(end - self._overlap_chars, start + 1)

        if not chunks:
            raise ValueError("knowledge document did not produce any chunks")
        return tuple(chunks)

    @staticmethod
    def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
        if hard_end >= len(text):
            return len(text)
        minimum = start + max((hard_end - start) // 2, 1)
        for separator in ("\n\n", "\n", "。", ". ", " "):
            boundary = text.rfind(separator, minimum, hard_end)
            if boundary >= minimum:
                return boundary + len(separator)
        return hard_end
