# Pre-registration — Experiment A

**Published 2026-08-12, before any question has run through either agent.**

This committed file is the authoritative copy. It is anchored by its git
commit, so any later change is visible in this repository's history rather
than silently replacing the text. A pre-registration whose wording can be
edited without trace is not a pre-registration.

Everything downstream is measured against this document.

## The question

When a research agent is gated through a pre-action verification layer that
can force a replan, how many of its citations survive independent
verification, compared with the same agent ungated?

The framing throughout is **which citations survive verification**. It is
never which LLM lies most. The unit of study is the citation and the gate,
not the model's character.

That distinction is not cosmetic. A "which model lies most" study is
unwinnable: it invites vendor rebuttal, depends on contestable prompt and
domain choices, and produces a leaderboard nobody trusts. A "which citations
survive verification" study is a reproducible measurement of an artifact
against a deterministic ground-truth stack. The output is a catch rate, not
an accusation.

## Precedent and the honesty norm

The AVeriTeC benchmark reports 57.6% overall on real-world claim
verification, with sub-categories disclosed rather than hidden: 70.6%
Supported, 61.6% Refuted, 27.3% Not-Enough-Evidence, 13.6%
Conflicting-Evidence (Schlichtkrull et al., NeurIPS 2023; summarized in the
AgentOracle whitepaper, https://agentoracle.co/whitepaper).

AVeriTeC is the precedent for two reasons: it shows automated verification
of real claims is hard and partial, and it sets the norm this experiment
follows — publish weak categories alongside strong ones. Experiment A
measures citation survival under a gate, not claim adjudication, but
inherits the discipline: state the ceiling, disclose the categories, do not
round up.

## Design

Two deep-research agents, identical except one variable. The same LLM drives
both within a run. The same 200 questions across four domains — finance,
medical, legal, technical — 50 per domain, **fixed before the run**.

- **Agent U (ungated)** runs its normal loop with no verification gate.
- **Agent G (gated)** runs the identical loop, but each proposed citation
  passes through AgentOracle `/evaluate` before the answer finalizes. On a
  failing verdict the agent replans rather than emitting the citation. The
  gate is fail-closed: an unverifiable citation does not ship.

The gate under test is `/evaluate`, which includes model-judged checks — not
the deterministic-only `/v1/verify-facts` endpoint, which is a separate
product surface outside this experiment's scope.

**AgentOracle `/evaluate` is the gate being measured, not the scorer.** This
is the load-bearing constraint of the design. The gate never grades its own
homework, and no AgentOracle output is used to label any citation.

## Independent, deterministic ground truth

Ground truth is deterministic first, human second, and the product is never
the judge. This stack is locked at publication and does not change.

The deterministic layer scores each citation mechanically: the URL resolves
(HTTP 2xx live, or retrievable from the Wayback Machine), the DOI exists
(resolves in Crossref, where present), and the cited page string-contains or
semantic-matches the claim above a fixed, named, frozen threshold. Every
label is a function a third party can recompute from raw data — no human and
no LLM-as-judge in the loop.

A stratified random 10% of citations goes to human review, blind-labeled by
an independent reviewer who does not know whether a citation came from Agent
U or Agent G, against the same criteria as a rubric. This measures the
deterministic layer's own error rate. Divergence is reported, not smoothed
over.

Full criteria: [`ground-truth-spec.md`](ground-truth-spec.md).

## Metrics

**Citation survival rate** = supported / total citations emitted, reported
per domain and overall, for Agent U and Agent G separately. Weak categories
publish with the rest.

**The correction rate.** Beyond survival, the gated agent's runs record a
distinct outcome class: a citation that received a failing verdict,
triggered a replan, and was replaced by a citation that subsequently passed
both the gate and the independent ground truth. This is its own headline
number, separate from the block rate. A random blocker can stop a bad
citation; only a gate whose objection carries usable information lets the
agent fix its work. Reported per domain and overall, across all base models.

**Gate accuracy — `/evaluate` measured directly as a classifier.**

Survival rate and correction rate both measure the gate *and* the agent's
replan behavior together. They cannot tell you how good the gate is on its
own. This metric does.

For every citation `/evaluate` inspects, its verdict (pass / fail) is compared
against that same citation's independent ground-truth label. Reported per
domain, per base model, and overall:

- **Precision** — of the citations the gate passed, how many survived ground truth.
- **Recall** — of the citations that fail ground truth, how many the gate caught.
- **The full confusion matrix** — true/false positives and negatives, as raw counts, not just derived rates.

**This requires ground-truthing citations the gate blocked.** A blocked
citation is never emitted, so under a survival-rate-only design it is never
labeled and its correctness is never known. Every citation the gate *saw* goes
to ground truth, including the ones it rejected. Blocked-citation labels
publish in the raw JSONL with the rest.

**The false-positive rate is the number that matters most, and it is the one
survival rate structurally hides.** A fail-closed gate that rejects good
citations looks identical in a survival-rate table to one that rejects only
bad ones — both raise the survival rate of what ships. But over-blocking
destroys usable evidence and makes a gate unusable in production. Publishing
precision and recall means the gate's cost is visible alongside its benefit.

**Amendment provenance.** This metric was not in the design published earlier
today. It was added 2026-08-12, **before harness completion and before any
data collection**, after [@babyblueviper1](https://x.com/babyblueviper1) asked
whether direct gate accuracy was in scope or deliberately out. It was out by
omission, not by intent. Adding a metric before any data exists is a
legitimate amendment; adding one after would not be. The change is visible in
this repository's commit history, per the anchoring rule stated at the top of
this document.

## Model independence

A result that only holds for one base model is a curiosity. The full
experiment runs across **three version-pinned models from three provider
families: GPT-5.6, kimi-k3, and claude-sonnet-5.** Exact version pins publish
in the harness lockfile before the first run.

**Selection rule.** Models are selected for documented API availability and
version pinning through the run window — **not benchmark rank.** A model
that cannot be pinned for the duration cannot carry a pre-registered result.

**Deviation rule.** If a model is withdrawn or changed mid-window, that is
published as a **pre-registered deviation, never a silent substitution.** The
deviation notice states which model, when, and what replaced it.

**Reporting.** Catch rate and confidence interval are reported per model.
**Between-model differences are a primary result, not a failure to hide.**
Three provider families are the point: a catch rate that holds across
independent vendors is evidence about the gate; one that holds only on a
single family is evidence about that family.

## The commitment

Nothing is adjusted after data collection begins. The commitment is explicit
and public: **results in 3 weeks whatever they show.** A smaller improvement
than hoped, or none in a domain, publishes as-is.

**Two content moments, and only two:** this pre-registration, and the
results. From the first run day a page in this repository updates on a fixed
daily schedule with cumulative counts only — citations processed,
deterministic labels issued, receipts published — with no interpretation and
no interim conclusions until the pre-registered results date. No teaser
posts and no "early signal" claims in between.

## Open harness and evidence trail

The experiment is only as trustworthy as its reproducibility. The harness
releases under MIT in this repository. Raw data publishes alongside the
writeup: the full raw JSONL — every question, citation, deterministic label,
gate verdict, and the anonymized human-sample labels — not summarized away.
A reader can recompute every headline number from the raw file.

**Signed receipts as the experiment's own evidence trail.** Every gate
decision — pass, fail, replan — emits a signed verification-state receipt
(canonical bytes per RFC 8785, Ed25519 JWS), published alongside the raw
JSONL. A reader can verify offline that the published gate decisions are the
ones actually issued, without trusting our copy of the data. The
experiment's evidence is held to the standard the experiment is about.

## Schedule

| Date | Milestone |
|---|---|
| **2026-08-12** | Pre-registration published (this document) |
| 2026-08-17 | Harness code complete, MIT, in this repository |
| 2026-08-19 to 2026-08-26 | Run across three base models |
| **2026-09-02** | Results published, weak categories included, raw JSONL attached |

## Re-run it

Once results land: clone the harness, point it at your own model or your own
questions, and reproduce or contest the result. A benchmark that cannot be
independently rerun is an assertion; one that can be rerun is a record.

---

The gap between pre-registration and results is where credibility is earned
or lost, and the discipline is to stay quiet in that gap.

— Joe Krausz
