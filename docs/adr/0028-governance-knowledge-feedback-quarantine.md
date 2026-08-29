# ADR 0028: Governance knowledge feedback quarantine

- Status: Accepted for v0.27
- Date: 2026-08-25

## Context

v0.26 can publish verified remediation postmortems as advisory-only governance knowledge. Publication review prevents unsafe
free text at creation time, but production use still needs a bounded way to report relevance, accuracy, staleness or safety
problems discovered after publication. The response must not collect prompts, model output or executable text, and an
unreviewed report must not silently remove knowledge.

## Decision

Store feedback in PostgreSQL with independent read, report and review permissions. Each report binds the exact tenant,
handler, postmortem version, knowledge version and content fingerprint and stores only enumerated signal/reason values.
The reporter cannot review the same feedback. Review revalidates the current identity and locks both feedback and source
postmortem. Confirming a safety concern atomically marks the feedback confirmed and the postmortem quarantined. The
governance retriever continues to select only published postmortems, so quarantine takes effect immediately while content,
embedding and incident/remediation lineage remain available for audit. Other awaiting feedback for the same knowledge
version becomes `superseded` in the same transaction without recording a reviewer, because the quarantined source can no
longer support an independent review decision.

## Alternatives

1. Immediately hide knowledge when any report arrives. Rejected because a single unreviewed report could deny useful
   knowledge and bypass separation of duties.
2. Delete content and vectors after confirmed feedback. Rejected because deletion destroys evidence, prevents root-cause
   review and makes safe restoration impossible.
3. Store free-text comments or the original retrieval query. Rejected because those fields can contain credentials,
   personal data, prompt injection or executable instructions.

## Consequences

- Feedback reporting is bounded, versioned and tenant-isolated.
- Confirmed safety feedback becomes an immediate retrieval kill switch without physical deletion.
- Quarantine closes the same-version pending queue with an explicit `superseded` terminal state rather than leaving stale
  work or manufacturing reviewer identity.
- Duplicate reports by one Principal for one postmortem version are rejected by both service logic and a unique constraint.
- v0.27 Wave 3-4 adds the console queue, immutable quality snapshots, a 24-hour quarantine retention gate and independently approved versioned recovery; see ADR 0029.

## Reversal conditions

Replace this design only if an external knowledge-governance control plane provides equivalent tenant isolation, immutable
lineage, independent review, transactional quarantine and runtime exclusion. External feedback must never become the
authorization or execution authority.
