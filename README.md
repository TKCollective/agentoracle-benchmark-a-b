# AgentOracle Benchmark A/B — Experiment A

Measuring citation survival under a fail-closed verification gate.

## What this measures

Two deep-research agents, identical except for one variable: Agent U runs
ungated; Agent G routes each proposed citation through AgentOracle
`/evaluate` before finalizing an answer, and replans on a failing verdict.
The measured quantity is citation survival under independent ground truth,
per domain and overall. Framing: which citations survive verification —
never which LLM lies most.

## Status

- [x] Pre-registration published (Aug 12) — `docs/pre-registration.md`
- [x] Question set frozen (Aug 17) — 200 questions, sha256 `f7f70bd9…e7a5` over JCS canonical bytes, see the question-set freeze amendment
- [x] Harness code complete (Aug 17) — MIT, unattended, resumable, cron-safe
- [ ] Run across three base models (Aug 19–26)
- [ ] Results published, raw JSONL included (Sep 2)

## Metrics

Citation survival rate · correction rate · **gate accuracy** (`/evaluate`
precision, recall, and confusion matrix against independent ground truth,
isolating the gate from the agent's replan behavior). Every citation the gate
saw is graded, **including the ones it blocked** — otherwise the
false-positive rate is unmeasurable.

## Ground truth (locked)

Deterministic first, human second. See `docs/ground-truth-spec.md`.
AgentOracle `/evaluate` is the gate under test, not the scorer.

## Model independence

The same 200 questions run across three version-pinned models from three
provider families: **GPT-5.6, kimi-k3, and claude-sonnet-5**. Exact pins
publish in the harness lockfile before the first run.

Catch rate and confidence interval are reported per model.
**Between-model differences are a primary result, not a failure to hide.**

## Live results page

From the first run day, a page in this repository updates daily with
cumulative counts only — citations processed, deterministic labels issued,
receipts published — and no interpretation until the pre-registered results
date.

## Reproduce it

Clone this repo, point the harness at your own model or your own
question set, and rerun or contest the result. See `docs/repro.md`.

## License

MIT. See `LICENSE`.

## Evidence trail

Every gate decision (pass/fail/replan) emits a signed verification-state
receipt (canonical bytes per RFC 8785, Ed25519 JWS). The full receipt set
publishes alongside the raw JSONL so results can be checked offline.

## Sources

- Pre-registration design (authoritative, committed copy):
  [`docs/pre-registration.md`](docs/pre-registration.md)
- AVeriTeC (Schlichtkrull et al., NeurIPS 2023), summarized in the
  AgentOracle whitepaper: https://agentoracle.co/whitepaper

---

## Amendment — 2026-08-27

Append-only. Nothing above this line has been edited. The pre-registration, the frozen
question-set hash, and the original Status checklist are preserved as published so the
trail remains checkable against what was committed in advance.

**Collection closed 2026-08-25 at 593 of 600, with 7 documented-unresolved.**

**The pre-registered headline finding is withdrawn.** The gated arm measured a degraded
service, so the quantity the pre-registration set out to measure was not the quantity the
run produced. Withdrawing it is the pre-registration working as intended: the design was
frozen before the data, and the data does not support the headline.

**What publishes on 2026-09-02** is the null result, together with the service-integrity
defect that caused it, and the raw JSONL.

The two unticked boxes in the Status checklist above are superseded by this amendment and
are deliberately left unticked rather than rewritten.
