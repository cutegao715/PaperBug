# Review records and continuity

Use the existing conversation, manuscript knowledge, evidence cards, materials and draft versions. Do not create a Task, independent execution cursor, or imported manuscript_state. An ordinary short review can remain in the conversation. Persistent reports and tracked issues are optional.

Read current text with read_draft, retaining its version hash. To save a memo use save_paper_review: title, scope, round_label, modules actually used, artifacts=[{filename,expected_sha256,role}], knowledge_ids, sources, checks, findings and summary. Roles distinguish manuscript/companion from prior_comments/response. Checks record dimension, coverage and basis. Findings include title, location, problem, basis, action_needed, severity, evidence_status, classification and affected_artifacts. Empty findings are valid. Never save a report against unread text or use a hash as evidence of semantic coverage.

Use read_context(kind=paper_reviews), optionally key=<report_id>, for full reports and follow-up history. Prior report sources are immutable snapshots: read_context(kind=paper_review_source,key=<report_id>,resource_id=<filename>) returns the historical text in pages. Use current read_draft for the active candidate. Linking prior_report_id establishes provenance, not proof of cross-round resolution.

For an issue worth revisiting, record_review_followup uses report_id, issue_id, expected_revision, status, reason, sources and verifications. A resolved assessment needs located verbatim evidence in an affected manuscript/companion file: {filename,expected_sha256,locator,quote}. A response letter alone is insufficient. Matching a quote verifies its presence and version, not whether it resolves the scientific concern.

Agent follow-ups remain proposed. Users may adopt them or record their own assessments. Do not claim that a proposal, including a proposed waiver, changed the effective issue status. Source, manuscript or linked knowledge changes invalidate reuse of prior resolution; preserve the historical assessment and inspect the new evidence.

Only paper-specific terminology or argument decisions belong in manuscript knowledge, with its existing proposal/adoption rules. Reviewer opinions and findings remain review records. Shared resources contain methods, never private facts or one supervisor's preferences. Apply private standards only when explicitly established in the current workspace.
