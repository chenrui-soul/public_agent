from __future__ import annotations

import math
from uuid import uuid4

import pytest

from public_agent.knowledge import (
    DeterministicHashEmbeddingProvider,
    DocumentSource,
    KnowledgeDocumentInput,
    KnowledgeDocumentRecord,
    KnowledgeFileInput,
    KnowledgeIngestionService,
    TextChunker,
)
from public_agent.knowledge.base import PreparedKnowledgeDocument


class CapturingKnowledgeWriter:
    def __init__(self) -> None:
        self.documents: list[PreparedKnowledgeDocument] = []

    async def publish(self, document: PreparedKnowledgeDocument) -> KnowledgeDocumentRecord:
        self.documents.append(document)
        source = document.document
        return KnowledgeDocumentRecord(
            id=uuid4(),
            tenant_id=source.tenant_id,
            agent_id=source.agent_id,
            domain_id=source.domain_id,
            namespace=source.namespace,
            source_key=source.source_key,
            title=source.title,
            version=source.version,
            content_hash=document.content_hash,
            chunk_count=len(document.chunks),
            status="active",
        )


def knowledge_document(content: str) -> KnowledgeDocumentInput:
    return KnowledgeDocumentInput(
        tenant_id="tenant-a",
        agent_id="support-agent",
        domain_id="support-agent",
        namespace="support-manuals",
        source_key="refund-policy",
        title="Refund policy",
        content=content,
    )


def test_text_chunker_preserves_order_and_overlap() -> None:
    text = " ".join(f"word-{index}" for index in range(80))
    chunks = TextChunker(max_chars=120, overlap_chars=20).chunk(text)

    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.end_char > chunk.start_char for chunk in chunks)
    assert chunks[1].start_char < chunks[0].end_char


def test_text_chunk_offsets_reference_normalized_source() -> None:
    text = "  first paragraph\r\n\r\nsecond paragraph  "
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = TextChunker(max_chars=100, overlap_chars=10).chunk(text)

    assert len(chunks) == 1
    assert normalized[chunks[0].start_char : chunks[0].end_char] == chunks[0].content


@pytest.mark.asyncio
async def test_ingestion_prepares_fixed_dimension_embeddings() -> None:
    writer = CapturingKnowledgeWriter()
    embeddings = DeterministicHashEmbeddingProvider()
    service = KnowledgeIngestionService(
        writer=writer,
        embeddings=embeddings,
        chunker=TextChunker(max_chars=120, overlap_chars=20),
    )

    record = await service.ingest(
        knowledge_document("Refund requests are accepted for thirty days. " * 12)
    )

    prepared = writer.documents[0]
    assert record.chunk_count == len(prepared.chunks)
    assert prepared.embedding_profile == embeddings.profile
    assert all(len(chunk.embedding) == embeddings.profile.dimensions for chunk in prepared.chunks)
    assert all(
        math.isclose(
            math.sqrt(sum(value * value for value in chunk.embedding)),
            1.0,
            rel_tol=1e-6,
        )
        for chunk in prepared.chunks
    )


@pytest.mark.asyncio
async def test_file_ingestion_parses_and_preserves_source_audit_metadata() -> None:
    writer = CapturingKnowledgeWriter()
    service = KnowledgeIngestionService(
        writer=writer,
        embeddings=DeterministicHashEmbeddingProvider(),
    )

    record = await service.ingest_file(
        KnowledgeFileInput(
            tenant_id="tenant-a",
            agent_id="support-agent",
            domain_id="support-agent",
            namespace="support-manuals",
            source_key="refund-policy",
            source=DocumentSource(
                filename="refund-policy.html",
                media_type="text/html",
                content=(
                    b"<html><head><title>Refund Policy</title></head>"
                    b"<body><script>ignore()</script><p>Refunds last 30 days.</p></body></html>"
                ),
            ),
            metadata={"owner": "support"},
        )
    )

    prepared = writer.documents[0]
    assert record.title == "Refund Policy"
    assert prepared.document.content == "Refunds last 30 days."
    assert prepared.document.metadata["owner"] == "support"
    parser_metadata = prepared.document.metadata["document_parser"]
    assert parser_metadata["filename"] == "refund-policy.html"
    assert parser_metadata["media_type"] == "text/html"
    assert parser_metadata["parser_profile"] == "html-text-v1"
    assert len(parser_metadata["source_hash"]) == 64


@pytest.mark.asyncio
async def test_hash_embedding_favors_shared_terms() -> None:
    embeddings = DeterministicHashEmbeddingProvider()
    query = await embeddings.embed("refund window")
    related = await embeddings.embed("the refund window is thirty days")
    unrelated = await embeddings.embed("database connection pool tuning")

    related_similarity = sum(left * right for left, right in zip(query, related, strict=True))
    unrelated_similarity = sum(
        left * right for left, right in zip(query, unrelated, strict=True)
    )
    assert related_similarity > unrelated_similarity


@pytest.mark.asyncio
async def test_ingestion_rejects_documents_over_chunk_limit() -> None:
    service = KnowledgeIngestionService(
        writer=CapturingKnowledgeWriter(),
        embeddings=DeterministicHashEmbeddingProvider(),
        chunker=TextChunker(max_chars=100, overlap_chars=10),
        max_chunks=1,
    )

    with pytest.raises(ValueError, match="chunk ingestion limit"):
        await service.ingest(knowledge_document("refund policy " * 40))


@pytest.mark.asyncio
async def test_ingestion_rejects_non_json_metadata() -> None:
    service = KnowledgeIngestionService(
        writer=CapturingKnowledgeWriter(),
        embeddings=DeterministicHashEmbeddingProvider(),
    )
    invalid = knowledge_document("refund policy").model_copy(
        update={"metadata": {"unsupported": object()}}
    )

    with pytest.raises(ValueError, match="JSON serializable"):
        await service.ingest(invalid)


def test_chunker_rejects_blank_documents() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        TextChunker().chunk("   \n\n")
