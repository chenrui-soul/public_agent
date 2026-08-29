"""Stable knowledge-management errors shared by storage and HTTP adapters."""


class KnowledgeNotFoundError(KeyError):
    """The requested knowledge resource is outside the resolved scope or absent."""


class KnowledgeIdempotencyConflictError(ValueError):
    """An idempotency key was reused for a different immutable request."""


class KnowledgeStepInProgressError(RuntimeError):
    """Another worker owns the current bounded ingestion step."""


class KnowledgeStepOwnershipLostError(RuntimeError):
    """A stale or expired worker attempted to mutate an ingestion job."""


class KnowledgeDocumentStateError(ValueError):
    """A document management operation is invalid for the current lifecycle state."""


class KnowledgeCursorError(ValueError):
    """A document pagination cursor is malformed or unsupported."""
