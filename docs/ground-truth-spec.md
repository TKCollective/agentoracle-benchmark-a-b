# Ground-truth criteria (locked at pre-registration)

Deterministic first, human second. A citation is graded by the first
criterion that applies; graders never see which agent produced it.

## Tier 1 — deterministic

Machine-checkable, no judgment:

1. **Resolves** — the URL or DOI returns 2xx and is not a soft-404.
2. **Exists as claimed** — DOI resolves to a record whose title and year
   match the citation within a normalized string comparison.
3. **Retrievable** — the page body or PDF text is obtainable for Tier 2.

A citation failing any Tier 1 check is `fabricated` or `broken` and does
not proceed to Tier 2.

## Tier 2 — support check

Does the retrieved source contain the claim attached to it?

- `supported` — the claim is stated or directly entailed.
- `partially_supported` — a weaker or narrower version is stated.
- `unsupported` — the source is real and retrievable but does not contain
  the claim.
- `contradicted` — the source states the opposite.

## Tier 3 — blind human adjudication

Applies only where Tier 2 is ambiguous (near-threshold semantic match, or
graders disagree). Reviewers see the claim and the source, never the agent
identity. Adjudications ship as raw JSONL with the results.

## Reported quantity

Citation survival rate = `supported / total_citations_emitted`, reported
per domain (finance, medical, legal, technical) and overall, for Agent U
and Agent G separately. Weak categories publish with the rest.

## What the gate is not

AgentOracle `/evaluate` is the intervention under test. It is never the
scorer. No AgentOracle output is used to grade any citation.
