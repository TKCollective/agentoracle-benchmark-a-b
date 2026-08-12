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
- [ ] Harness code complete (target Aug 17)
- [ ] Run across three base models (Aug 19–26)
- [ ] Results published, raw JSONL included (Sep 2)

## Ground truth (locked)

Deterministic first, human second. See `docs/ground-truth-spec.md`.
AgentOracle `/evaluate` is the gate under test, not the scorer.

## Model independence

Rerun on the same 200 questions with GPT-5.4 and Kimi K2.6 as the base
model driving both agents, to test whether the failure class and catch
rate generalize.

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
