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

## Model substitution amendment

**Dated 2026-08-19. Published before the first run and before any data
collection, under the deviation rule stated in this document.**

The design named three model families: GPT-5.6, kimi-k3, and claude-sonnet-5.
At pinning time, **kimi-k3 could not satisfy the selection rule published
above.**

Moonshot's model documentation states no versioning or pinning guarantee, does
not say whether weights or configuration may change under the same model id, and
publishes no dated snapshot ids for `kimi-k3`
([platform.kimi.ai/docs/models](https://platform.kimi.ai/docs/models)). We
therefore cannot assert that `kimi-k3` on 2026-08-26 is the same artifact as
`kimi-k3` on 2026-08-19. The rule decides the case: *"A model that cannot be
pinned for the duration cannot carry a pre-registered result."*

The same page announces a full platform sunset on 2026-08-31 for `kimi-k2.5` and
the `moonshot-v1` series — four days after this run window closes. That is not K3
itself, but it establishes a provider that retires model ids on short public
notice, which is the risk this rule exists to exclude.

**Considered and rejected for this run.** K3 open weights were published
2026-07-26 under a Modified MIT license, and a specific weights revision is
content-addressable and therefore strictly more pinnable than any hosted id.
Running open weights is different infrastructure than the hosted-API client this
harness implements, so it is out of scope for this run rather than unattractive.

**Substituted: `mistral-medium-3-5`** — General Availability, version string
`GAv26.04`, released 2026-04-28, 256k context. It satisfies the rule on stronger
evidence than the incumbent pins. Mistral publishes a lifecycle policy stating
that General Availability models *"Receive no silent updates"*, documents the
`model-name-major-minor` form as a *"Fixed major and minor version"*, and
instructs: *"For precise control, pin your deployment to a specific major.minor
version identifier."* The GA deprecation notice period is six months, so the
model cannot be withdrawn inside this window, and retirement fails loudly —
*"Once retired, requests to its identifiers fail with a 404 error"* — rather than
by silent substitution
([docs.mistral.ai/inference/model-lifecycle](https://docs.mistral.ai/inference/model-lifecycle)).

Selection contains no benchmark-performance reasoning, per the rule. This model
was not chosen because it scores well; it was chosen because its pinning is
documented rather than assumed. One additional property is worth recording
without treating it as a criterion: because the weights are published under a
Modified MIT license, the exact artifact is independently obtainable, so a third
party can recompute against the same model rather than trusting our
description of it.

**Also corrected in the same commit.** The configuration named `gpt-5.6`, which
is an **alias** — the provider's changelog states *"the `gpt-5.6` alias routes
requests to `gpt-5.6-sol`."* Pinning an alias would have violated this same
rule, silently, since an alias can be repointed mid-window. The pin is now
`gpt-5.6-sol`. **Disclosed limit:** no dated snapshot is published for that
model, so the model id is the finest pinning granularity the provider offers, and
we pin what is pinnable rather than implying more precision than exists.

The harness now **refuses to run** if any pin matches a known provider alias,
rather than warning. A misconfigured alias cannot carry a pre-registered result.

This amendment follows the standard applied to the gate-accuracy metric and the
question-set freeze: a constraint discovered before any data exists is published
as a dated deviation, never absorbed silently. No question had been run through
either agent at the time of this entry.

## Question-set freeze amendment

**Dated 2026-08-17. Published before the first run and before any data
collection, under the deviation rule stated in this document.**

At publication on 2026-08-12, `harness/questions/questions.json` declared
`"total": 200` over an empty `questions` array, and carried this note:

> `"Frozen at pre-registration. Any change after Aug 12 invalidates the run."`

**That note was wrong, and stricter than anything this document requires.** The
design above commits the 200 questions to being *"fixed before the run"* — and
the schedule below sets the run at 2026-08-19, with harness completion on
2026-08-17. The note misstated a freeze-before-the-run commitment as a
freeze-at-publication commitment, which was never the design.

**What changed.** The 200 questions were written and committed on 2026-08-17,
before the first run and before any data collection. The note was replaced with
one that states the actual commitment and makes it checkable.

**Why this is a legitimate amendment.** It follows the same test applied to the
gate-accuracy metric earlier in this document: that metric was added
2026-08-12, before harness completion and before any data collection, and the
reasoning recorded there was that *"adding a metric before any data exists is a
legitimate amendment; adding one after would not be."* The same standard
applies here. No question has been run through either agent. Correcting a note
that overstated a constraint, before any data exists, does not move a goalpost —
it removes a claim we could not honour and did not need to make.

**The upgrade: the freeze is now an artifact rather than a sentence.** A note
saying "frozen" is an assertion. Instead:

```
sha256 of the RFC 8785 (JCS) canonical bytes of the .questions array
  = f7f70bd92dc284adaeb2580117e324cf379fa4beae9a9a2c5fc0bd40aefee7a5
canonical byte length = 56403
```

Recompute it with the canonicalizer shipped in this repository:

```python
import json, hashlib
from harness.receipts.receipt_writer import canonicalize
q = json.load(open("harness/questions/questions.json"))["questions"]
print(hashlib.sha256(canonicalize(q)).hexdigest())
```

The hash covers the `questions` array only, not the whole file, so the note
recording the hash cannot alter it. Any later change to the question set
changes this digest and must publish as its own dated deviation. This is the
same move the verification work makes everywhere else: replace a claim about a
record with a record that can be checked without trusting us.

## Execution-environment amendment

**Dated 2026-08-20. Published before the first live call and before any data
collection, under the deviation rule stated in this document.**

Local pre-flight on the operator's runner surfaced five execution-environment
defects, none touching the design. Corrected in one amendment:

1. **`--model` is now required.** The CLI previously defaulted to `gpt-5.6`, a
   retired provider alias the harness itself refuses. A pre-registered run must
   name its model explicitly; a silent default has no place in one.
2. **`--auth-mode` now exists.** The documented `env_key` reproduction path had
   no CLI flag wiring it through; added, with the mode recorded in run
   metadata. The sampling record now reports the mode actually in use rather
   than a hard-coded value.
3. **Published-run authentication is `env_key` on the operator's local
   machine**, not `proxy_injected` as earlier drafts of `repro.md` stated.
   Credential values never enter the repository, logs, or metadata.
4. **`httpx==0.28.1` is pinned** (with its transitives), the version that
   validated the live transport on 2026-08-19; it was required by the live
   adapters but absent from `requirements.txt`.
5. **Run metadata now records the runtime** (Python version, implementation,
   platform), the auth mode, the sampling record, and the receipt-signing
   identity (`kid`, algorithm, `dev_key` flag, public-JWK path). The published
   run signs with a run-specific benchmark key whose `kid` is prefixed
   `benchmark-a-`; the production AgentOracle signing key never leaves its
   deployment, and benchmark receipts are distinguishable from production
   receipts by construction.

Nothing in this amendment touches the frozen questions, the model pins, the
prompts, the thresholds, the metrics, or the scoring rules. The question-set
digest is unchanged:
`f7f70bd92dc284adaeb2580117e324cf379fa4beae9a9a2c5fc0bd40aefee7a5`.

The standard applied is the one this document already used twice: a defect
found before any data exists is published as a dated deviation, never absorbed
silently. No live call had been made at the time of this entry.

## Operational amendment - endpoint, execution-failure semantics, sampling compatibility

**Dated 2026-08-20 (second amendment of this date). Published before the first
collected data point, under the deviation rule stated in this document.** Three
defects surfaced during a five-question live smoke test (run id `smoke-test`,
discarded); none had touched collected data because none existed.

1. **Gate endpoint (defect #8).** The client default pointed at
   `https://api.agentoracle.co/evaluate`, a hostname with no DNS record. The
   gate is served from `https://agentoracle.co/evaluate`. The default is
   corrected and the effective endpoint is now recorded in run metadata as
   `gate_endpoint`.

2. **Execution-failure semantics (defect #9).** The harness marked a question
   complete, and the invocation exited 0, even when every provider call for the
   question failed - observed twice, ten consecutive failures each, zero
   citations, zero receipts, zero gate calls. That silently converts total
   failure into apparent success: the exact defect class this benchmark exists
   to measure. Corrected: an arm error (transport, HTTP, parse, timeout,
   protocol) is an execution failure - never marked complete, no receipt or
   gate decision fabricated, the attempt recorded in resumable state
   (`execution_failures`) and in the results file under
   `schema: experiment-a/execution-error/1`, and the question stays pending so
   a rerun retries it. Exit codes distinguish the outcomes: `3` when nothing
   completed, `4` when some completed and some failed. Three facts the record
   now keeps separate: *the provider call failed*, *the model completed and
   proposed zero citations*, and *the model proposed citations and all failed
   the gate*.

3. **Sampling-parameter compatibility (defect #10,
   `2026-08-20-sampling-parameter-compatibility`).** The Anthropic API rejects
   the `temperature` parameter for `claude-sonnet-5` outright (HTTP 400
   `invalid_request_error`, observed live 2026-08-20). The pre-registered
   intent is minimum-variance sampling, not semantic equality of a parameter
   name across incompatible provider APIs. Ruling: each family receives the
   strongest supported minimum-variance configuration; `temperature=0.0` is
   sent where accepted (openai, mistral), omitted where rejected (anthropic,
   provider-default sampling in effect); run metadata records per family what
   was requested, sent, accepted or rejected (verbatim rejection preserved),
   and the effective control. The cross-family difference is reported, never
   hidden.

**Gate backend during the collection window.** The gate is model-backed - it
makes retrieval calls per invocation - so the gated arm is statistically
reproducible, not bit-reproducible. A reproducer running after the backend
changes gets a differently-backed gate rather than an error: the failure mode
is silent divergence, not a dead URL. The backend as it stood during collection
is recorded here.

Nothing in this amendment touches the frozen questions, the model pins, the
prompts, the thresholds, the metrics, or the scoring rules. The question-set
digest is unchanged:
`f7f70bd92dc284adaeb2580117e324cf379fa4beae9a9a2c5fc0bd40aefee7a5`.

**Also corrected on 2026-08-17, on a separate surface.** The changelog entry at
`agentoracle.co/changelog` for 2026-08-12 stated that the pre-registration
document, harness, and questions were all public before any data was collected.
The harness was a disclosed stub and the question set was empty, both scheduled
below for 2026-08-17. That entry now carries a dated correction quoting its
original wording.

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
