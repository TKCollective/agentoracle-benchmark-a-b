# Re-run it yourself

A benchmark that cannot be independently rerun is an assertion. One that
can be rerun is a record.

Everything below runs from a clean clone. `--dry-run` needs no API keys, makes
no network calls, and touches neither the gate nor any model provider.

## 1. Requirements

* Python 3.12 (3.11 works; 3.12 is what CI uses)
* A POSIX filesystem — the run lock uses `flock(2)`

```bash
git clone https://github.com/TKCollective/agentoracle-benchmark-a-b.git
cd agentoracle-benchmark-a-b
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r harness/requirements.txt
```

## 2. Smoke test (offline, no keys, no data collection)

```bash
python3 harness/run.py --dry-run --limit 5
echo $?          # 0
```

Under `--dry-run` the base model is replaced by a deterministic offline fixture
client and `/evaluate` by a deterministic offline verdict fixture. The full loop
executes — plan, propose citations, gate each one, fail-closed replan, emit
signed receipts, append raw JSONL — with no live calls. Repeating a dry run with
the same flags produces the same verdict sequence.

## 3. What gets written

| Path | Contents |
|---|---|
| `data/experiment-a-<run-id>.jsonl` | one raw record per question: both arms, every emitted citation, every blocked citation, every gate verdict |
| `data/state/<run-id>.state.json` | resume state; rewritten atomically after each question |
| `data/locks/<run-id>.lock` | advisory run lock |
| `data/receipts/receipts-<run-id>-*.jsonl` | one signed receipt per gate decision (pass, fail, replan) |
| `data/receipts/public_key.jwk.json` | Ed25519 public key for offline receipt verification |

No ground-truth label is ever written by the harness. Labels are produced by the
independent deterministic stack described in
[`ground-truth-spec.md`](ground-truth-spec.md) and joined to the raw JSONL on
`citation_id`. No `/evaluate` output is used as a label.

## 4. A full run

```bash
# one model, one domain
python3 harness/run.py --model gpt-5.6 --domain finance --resume

# one model, all 200 questions
python3 harness/run.py --model gpt-5.6 --domain all --resume

# all three provider families
for m in gpt-5.6 kimi-k3 claude-sonnet-5; do
  python3 harness/run.py --model "$m" --domain all --resume
done
```

Live runs require, per model family, `OPENAI_API_KEY`, `MOONSHOT_API_KEY` or
`ANTHROPIC_API_KEY`, plus `AGENTORACLE_API_KEY` for the gate. A live run also
requires an exact version pin for the model in
`harness/models/model_config.yaml`; an unpinned model is refused rather than run
(see [`model-independence.md`](model-independence.md), selection rule).

### Flags

| Flag | Meaning |
|---|---|
| `--model` | model id from `harness/models/model_config.yaml` |
| `--domain` | `finance` \| `medical` \| `legal` \| `technical` \| `all` (comma-separated accepted) |
| `--limit N` | process at most N *remaining* questions this invocation |
| `--resume` | continue an existing run (the default; explicit for cron clarity) |
| `--fresh` | archive existing state/results for this run id and start over |
| `--dry-run` | offline fixtures only |
| `--output` | output directory, or an explicit `.jsonl` path |
| `--arms U,G` | which arms to run |
| `--max-replans` | Agent G replan budget per research step (default 2) |
| `--citations-per-step` | candidate citations proposed per step (default 2) |
| `--delay-ms` | pacing delay between questions |
| `--run-id` | override the derived run id |
| `--questions` | point the harness at your own question file |

Every flag also has an env equivalent (`HARNESS_MODEL`, `HARNESS_DOMAIN`,
`HARNESS_LIMIT`, `HARNESS_OUTPUT`, `HARNESS_RUN_ID`, `HARNESS_DELAY_MS`,
`HARNESS_LOG_LEVEL`), so the harness runs unattended from a unit file or cron
entry with no arguments.

## 5. Interrupt and resume

Progress is durable per question: the raw JSONL line is written and `fsync`ed,
then the state file is atomically replaced. Kill the process at any point and
re-invoke the same command with `--resume`; it continues from the next
unprocessed question and never re-runs or double-counts a completed one.

```bash
python3 harness/run.py --dry-run --domain medical --delay-ms 700 --run-id demo &
sleep 3; kill -9 %1
python3 harness/run.py --dry-run --domain medical --resume --run-id demo
echo $?          # 0
```

## 6. Cron safety

```cron
*/15 * * * * cd /srv/benchmark-a-b && .venv/bin/python harness/run.py --model gpt-5.6 --resume >> /var/log/exp-a.log 2>&1
```

* An exclusive `flock` on `data/locks/<run-id>.lock` means a second concurrent
  invocation exits 0 immediately instead of corrupting state.
* When nothing is pending the process logs "nothing left to do" and exits 0.
* `SIGTERM`/`SIGINT` finish the question in flight, then exit 0 cleanly.
* The question-set digest is recorded in the state file; if `questions.json`
  changes mid-run the harness refuses to resume rather than mixing sets.

## 7. Verify the receipts offline

Every gate decision — pass, fail, replan — is a receipt whose payload is
RFC 8785 (JCS) canonical JSON signed as an Ed25519 (`EdDSA`) compact JWS.
Verification needs only the published public key, not our copy of the data:

```bash
python3 - <<'PY'
import glob, json
from harness.receipts.receipt_writer import verify_file
jwk = json.load(open("data/receipts/public_key.jwk.json"))
for f in sorted(glob.glob("data/receipts/receipts-*.jsonl")):
    print(f, verify_file(f, jwk))
PY
```

`verify_file` re-canonicalizes each payload and rejects any receipt whose bytes
are not already canonical, so a re-serialized or edited receipt fails.

Receipts written without `AGENTORACLE_RECEIPT_SK` in the environment are signed
with a local development key and carry `"dev_key": true` in the payload, so they
can never be mistaken for the published run's receipts.

## 8. Cumulative counts page

```bash
python3 harness/results_page/publish_daily.py --data-dir data --out results/daily.md
```

Counts only — questions run, citations emitted, deterministic labels issued,
receipts published. The module refuses to write output containing rates,
percentages or comparisons, because the pre-registration forbids interim
signal before the results date.

## 9. Question set

`harness/questions/questions.json` holds 200 questions, 50 per domain
(`fin-`, `med-`, `leg-`, `tec-`), frozen for the run. To contest the result with
your own questions, keep the same shape and pass `--questions yourfile.json`:

```json
{"id": "fin-001", "domain": "finance", "question": "...", "citation_expectation": "..."}
```

## 10. Recompute the headline numbers

The raw JSONL contains every citation the gate saw, including the ones it
blocked, so survival, correction, and gate precision/recall can all be
recomputed from the raw file plus the independent label file. Nothing in the
published tables is derived from data that is not in the raw output.

See [`pre-registration.md`](pre-registration.md) for the metric definitions and
[`ground-truth-spec.md`](ground-truth-spec.md) for the label criteria.
