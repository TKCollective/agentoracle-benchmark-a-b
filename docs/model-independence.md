# Model independence

The same 200 questions run across **three version-pinned models from three
provider families**:

| Model | Provider family |
|---|---|
| GPT-5.6 | OpenAI |
| kimi-k3 | Moonshot |
| claude-sonnet-5 | Anthropic |

**Exact version pins publish in the harness lockfile before the first run.**

## Selection rule

Models are selected for **documented API availability and version pinning
through the run window** — not benchmark rank. A model that cannot be pinned
for the duration of the run cannot carry a pre-registered result.

## Deviation rule

If a model is withdrawn or changed mid-window, that is published as a
**pre-registered deviation, never a silent substitution.** The notice states
which model, when it changed, and what replaced it.

## Reporting

Catch rate and confidence interval per model.

**Between-model differences are a primary result, not a failure to hide.**
Three provider families are the point: a catch rate holding across
independent vendors is evidence about the gate. One holding on a single
family is evidence about that family.

Stub sections beyond this land with the harness (target 2026-08-17).
