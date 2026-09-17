# Task 05 — Memory lifecycle and context assembly

Depends on: 01,03. Own: src/synthetic_lab/memory/, tests/memory/.

Implement local embeddings behind an adapter, scoped retrieval, recency lookup, exact entity lookup, bounded semantic ranking, summary expansion by source references, fact supersession, and ContextAssembler. Toolbox uses a fixed allowed set initially; embeddings do not choose which Python function executes.

Runtime events are the authoritative input for memory writes. Extracted model facts remain candidates until linked to observed or verified evidence. Preserve structured dates, entity IDs, and pending expectations outside summary-only storage. Save workflow memory only after verified successful completion.

Compute the input budget after reserving generation and safety space. Include every message/tool schema in accounting. Prefer trusted current state over obsolete facts, keep the current goal and observation, and compress older context with provenance. Report token accounting method and omitted items.

Acceptance: no cross-persona retrieval; shared facts require explicit scope; superseded facts are not presented as current; summary references recover original events; bounded context does not drop required expectations; malicious document text is treated as untrusted data; embedding-disabled mode is explicit. Use fake embeddings for unit tests and separately document real embedding validation.
