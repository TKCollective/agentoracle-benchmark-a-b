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

## Which citations get graded

**Every citation the gate saw — including the ones it blocked.**

A blocked citation is never emitted. If only emitted citations are graded, a
blocked citation's correctness is never known, and the gate's false-positive
rate is unmeasurable. Grading covers:

- every citation Agent U emitted;
- every citation Agent G emitted;
- **every citation Agent G proposed and the gate rejected.**

Rejected-citation labels publish in the raw JSONL alongside the rest. Graders
never see whether a citation was emitted, blocked, or which agent produced it.

## Reported quantities

**Citation survival rate** = `supported / total_citations_emitted`, reported
per domain (finance, medical, legal, technical) and overall, for Agent U
and Agent G separately. Weak categories publish with the rest.

**Gate accuracy** — `/evaluate`'s verdict compared against the independent
ground-truth label for the same citation: precision, recall, and the full
confusion matrix as raw counts, per domain and per base model. This isolates
the gate from the agent's replan behavior, which survival rate and correction
rate measure jointly. See `pre-registration.md`.

## What the gate is not

AgentOracle `/evaluate` is the intervention under test. It is never the
scorer. No AgentOracle output is used to grade any citation.
