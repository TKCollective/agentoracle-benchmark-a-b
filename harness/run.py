#!/usr/bin/env python3
"""Experiment A harness entry point.

Runs the frozen 200-question set through both arms — Agent U (ungated) and
Agent G (gated, fail-closed) — for one base model, and appends one raw JSONL
record per question.

Operational contract:

* **Unattended.** No prompts, ever. Every input comes from CLI flags or the
  environment. Failures on a single question are recorded and the run continues.
* **Resumable.** Progress is persisted after *each* question (results line
  fsynced, then the state file atomically replaced). An interrupted run resumes
  exactly where it stopped and never re-runs or double-counts a completed
  question.
* **Cron-safe.** An exclusive lockfile means two concurrent invocations cannot
  corrupt state — the second exits 0 immediately. When there is nothing left to
  do the process exits 0.

Usage (see ``docs/repro.md``)::

    python3 harness/run.py --dry-run --limit 5
    python3 harness/run.py --model gpt-5.6 --domain finance --resume

``--dry-run`` makes no network calls of any kind: no ``/evaluate`` request, no
provider API call, no data collection. Live collection begins on the
pre-registered date (2026-08-19).

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import logging
import os
import pathlib
import platform
import re
import signal
import sys
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.agents.agent_gated import GatedAgent  # noqa: E402
from harness.agents.agent_ungated import UngatedAgent  # noqa: E402
from harness.gate.evaluate_client import EvaluateClient  # noqa: E402
from harness.models.model_client import (  # noqa: E402
    ModelConfigError,
    get_model_client,
    load_model_specs,
)
from harness.receipts.receipt_writer import ReceiptWriter, load_or_create_key  # noqa: E402

QUESTIONS_PATH = REPO_ROOT / "harness" / "questions" / "questions.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data"
STATE_DIRNAME = "state"
LOCK_DIRNAME = "locks"

HARNESS_VERSION = "1.0.0"
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ALL_FAILED = 3   # nothing completed: every question hit execution failure
EXIT_PARTIAL = 4      # some completed, some failed; rerun retries the failures
EXIT_LOCKED = 0  # cron-safe: another invocation holds the lock; nothing to do

log = logging.getLogger("harness.run")

_STOP = {"requested": False}


def _handle_signal(signum, _frame):  # pragma: no cover - signal path
    _STOP["requested"] = True
    log.warning("received signal %s; finishing current question then exiting cleanly", signum)


# --------------------------------------------------------------------- helpers
def load_questions(path: pathlib.Path = QUESTIONS_PATH) -> Dict[str, Any]:
    doc = json.loads(path.read_text())
    qs = doc.get("questions", [])
    if not qs:
        raise SystemExit(
            f"{path} contains no questions. The question set is frozen before the run; "
            "populate it before invoking the harness."
        )
    return doc


def question_set_digest(questions: List[Dict[str, Any]]) -> str:
    """Digest of the frozen question set, recorded in every output file.

    If the set changes mid-run the digest changes, so a mixed run is detectable
    rather than silent.
    """
    blob = json.dumps(questions, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower() or "x"


class RunLock:
    """Exclusive advisory lock (``flock``) making repeated invocation safe."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                self._fh.close()
                self._fh = None
                return False
            raise
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps({"pid": os.getpid(), "started": time.time()}) + "\n")
        self._fh.flush()
        return True

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


class RunState:
    """Per-run progress file. Written atomically after every question."""

    def __init__(self, path: pathlib.Path, meta: Dict[str, Any]) -> None:
        self.path = path
        self.data: Dict[str, Any] = {
            "harness_version": HARNESS_VERSION,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated": None,
            "completed_question_ids": [],
            "counts": {
                "questions_run": 0,
                "citations_emitted": 0,
                "citations_blocked": 0,
                "citations_gate_saw": 0,
                "replans": 0,
                "receipts_written": 0,
                "questions_errored": 0,
            },
            "meta": meta,
        }
        self._loaded_from_disk = False

    # -- persistence
    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            disk = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            backup = self.path.with_suffix(".corrupt.json")
            self.path.replace(backup)
            log.error("state file was corrupt; moved to %s and starting a new state", backup)
            return False
        prev_digest = disk.get("meta", {}).get("question_set_digest")
        new_digest = self.data["meta"].get("question_set_digest")
        if prev_digest and new_digest and prev_digest != new_digest:
            raise SystemExit(
                "refusing to resume: the question set digest changed since this run started "
                f"({prev_digest[:12]} -> {new_digest[:12]}). The set is frozen for the run."
            )
        self.data = disk
        self._loaded_from_disk = True
        return True

    def save(self) -> None:
        self.data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # -- accessors
    @property
    def completed(self) -> set:
        return set(self.data["completed_question_ids"])

    @property
    def resumed(self) -> bool:
        return self._loaded_from_disk

    def record(self, question_id: str, results: List[Dict[str, Any]], receipts_written: int) -> bool:
        """Mark one question complete. Idempotent: a question already recorded
        is never counted twice."""
        if question_id in self.completed:
            return False
        c = self.data["counts"]
        for r in results:
            counts = r["counts"]
            c["citations_emitted"] += counts["citations_emitted"]
            c["citations_blocked"] += counts["citations_blocked"]
            c["citations_gate_saw"] += counts["citations_gate_saw"]
            c["replans"] += counts["replans"]
            if r.get("error"):
                c["questions_errored"] += 1
        c["questions_run"] += 1
        c["receipts_written"] = receipts_written
        self.data["completed_question_ids"].append(question_id)
        return True


def append_jsonl(path: pathlib.Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness/run.py",
        description="Experiment A: citation survival under a fail-closed verification gate.",
    )
    p.add_argument("--model", default=os.environ.get("HARNESS_MODEL", ""),
                   help="REQUIRED: base model id from harness/models/model_config.yaml "
                        "(no default; a pre-registered run must name its model explicitly)")
    p.add_argument("--domain", default=os.environ.get("HARNESS_DOMAIN", "all"),
                   help="finance | medical | legal | technical | all (default: all)")
    p.add_argument("--limit", type=int, default=int(os.environ.get("HARNESS_LIMIT", "0")),
                   help="process at most N remaining questions this invocation (0 = no limit)")
    p.add_argument("--resume", action="store_true",
                   help="continue an existing run (default behaviour; explicit for cron clarity)")
    p.add_argument("--fresh", action="store_true",
                   help="archive any existing state/results for this run key and start over")
    p.add_argument("--dry-run", action="store_true",
                   help="offline fixtures only: no /evaluate call, no provider API call, no data collection")
    p.add_argument("--output", default=os.environ.get("HARNESS_OUTPUT", str(DEFAULT_OUTPUT_DIR)),
                   help="output directory or explicit .jsonl path (default: data/)")
    p.add_argument("--questions", default=str(QUESTIONS_PATH), help="path to questions.json")
    p.add_argument("--run-id", default=os.environ.get("HARNESS_RUN_ID", ""),
                   help="override the derived run id (state and output are keyed on it)")
    p.add_argument("--arms", default="U,G", help="which arms to run (default: U,G)")
    p.add_argument("--max-replans", type=int, default=2, help="Agent G replan budget per research step")
    p.add_argument("--citations-per-step", type=int, default=2,
                   help="candidate citations the model proposes per research step")
    p.add_argument("--delay-ms", type=int, default=int(os.environ.get("HARNESS_DELAY_MS", "0")),
                   help="pacing delay between questions in milliseconds")
    p.add_argument("--gate-url", default=os.environ.get("AGENTORACLE_EVALUATE_URL", ""),
                   help="override the /evaluate endpoint")
    p.add_argument("--auth-mode", default=os.environ.get("HARNESS_AUTH_MODE", "proxy_injected"),
                   choices=["proxy_injected", "env_key"],
                   help="how live provider credentials are supplied (see docs/repro.md); "
                        "the published run used env_key on the operator's machine")
    p.add_argument("--log-level", default=os.environ.get("HARNESS_LOG_LEVEL", "INFO"))
    p.add_argument("--print-state", action="store_true", help="print the resolved state file and exit 0")
    return p


def resolve_paths(args: argparse.Namespace, run_id: str) -> Dict[str, pathlib.Path]:
    out = pathlib.Path(args.output)
    if out.suffix == ".jsonl":
        results = out
        base = out.parent
    else:
        base = out
        results = base / f"experiment-a-{run_id}.jsonl"
    return {
        "base": base,
        "results": results,
        "state": base / STATE_DIRNAME / f"{run_id}.state.json",
        "lock": base / LOCK_DIRNAME / f"{run_id}.lock",
        "receipts": base / "receipts",
    }


def derive_run_id(args: argparse.Namespace) -> str:
    if args.run_id:
        return slug(args.run_id)
    mode = "dryrun" if args.dry_run else "live"
    return f"{slug(args.model)}-{slug(args.domain)}-{mode}"


# ----------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    doc = load_questions(pathlib.Path(args.questions))
    questions = doc["questions"]
    digest = question_set_digest(questions)

    if args.domain and args.domain != "all":
        wanted = {d.strip() for d in args.domain.split(",")}
        unknown = wanted - set(doc.get("domains", []))
        if unknown:
            log.error("unknown domain(s): %s", ", ".join(sorted(unknown)))
            return EXIT_CONFIG
        questions = [q for q in questions if q["domain"] in wanted]

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    if not set(arms) <= {"U", "G"}:
        log.error("--arms accepts only U and/or G")
        return EXIT_CONFIG

    run_id = derive_run_id(args)
    paths = resolve_paths(args, run_id)

    if args.print_state:
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
        return EXIT_OK

    # --- model + gate wiring (validated before taking the lock)
    if not args.model:
        log.error(
            "no model chosen; pass --model <id>. Declared models: %s",
            ", ".join(s.model for s in load_model_specs()),
        )
        return EXIT_CONFIG
    try:
        model_client = get_model_client(args.model, dry_run=args.dry_run, auth_mode=args.auth_mode)
    except ModelConfigError as exc:
        log.error("model configuration error: %s", exc)
        log.error("declared models: %s", ", ".join(s.model for s in load_model_specs()))
        return EXIT_CONFIG

    lock = RunLock(paths["lock"])
    if not lock.acquire():
        log.info("another invocation holds %s; nothing to do", paths["lock"])
        return EXIT_LOCKED

    try:
        if args.fresh:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            for key in ("state", "results"):
                p = paths[key]
                if p.exists():
                    archived = p.with_name(p.name + f".archived-{stamp}")
                    p.replace(archived)
                    log.info("--fresh: archived %s -> %s", p, archived)

        signing_key = load_or_create_key(paths["receipts"])
        meta = {
            "run_id": run_id,
            "model": getattr(model_client, "name", args.model),
            "model_arg": args.model,
            "domain": args.domain,
            "arms": arms,
            "dry_run": bool(args.dry_run),
            "question_set_digest": digest,
            "question_count_in_scope": len(questions),
            "harness_version": HARNESS_VERSION,
            "results_path": str(paths["results"]),
            "runtime": {
                "python_version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "auth_mode": args.auth_mode,
            "sampling": getattr(model_client, "sampling", None),
            "receipt_signing": {
                "kid": signing_key.kid,
                "alg": "EdDSA",
                "dev_key": signing_key.dev_key,
                "public_jwk_path": str(paths["receipts"] / "public_key.jwk.json"),
            },
        }
        state = RunState(paths["state"], meta)
        existed = state.load()
        if existed:
            log.info(
                "resuming run %s: %s of %s questions already complete",
                run_id, len(state.completed), len(questions),
            )
        elif args.resume:
            log.info("--resume given but no prior state at %s; starting a new run", paths["state"])

        pending = [q for q in questions if q["id"] not in state.completed]
        if args.limit and args.limit > 0:
            pending = pending[: args.limit]

        if not pending:
            log.info("nothing left to do for run %s (%s questions complete)", run_id, len(state.completed))
            state.save()
            return EXIT_OK

        receipts = ReceiptWriter(receipt_dir=paths["receipts"], run_id=run_id, dry_run=bool(args.dry_run), key=signing_key)
        gate = EvaluateClient(
            endpoint=args.gate_url or None,
            dry_run=bool(args.dry_run),
        )

        meta["gate_endpoint"] = getattr(gate, "endpoint", args.gate_url or "")

        agents: List[Any] = []
        if "U" in arms:
            agents.append(
                UngatedAgent(
                    model=model_client,
                    citations_per_step=args.citations_per_step,
                    max_replans=0,  # the control arm has no gate to replan against
                    receipt_writer=None,
                )
            )
        if "G" in arms:
            agents.append(
                GatedAgent(
                    model=model_client,
                    gate=gate,
                    citations_per_step=args.citations_per_step,
                    max_replans=args.max_replans,
                    receipt_writer=receipts,
                )
            )

        log.info(
            "run %s | model=%s | arms=%s | dry_run=%s | pending=%s | results=%s",
            run_id, meta["model"], ",".join(arms), args.dry_run, len(pending), paths["results"],
        )

        # ReceiptWriter counts only what this invocation wrote; the state file
        # carries the cumulative total across invocations.
        receipt_baseline = int(state.data["counts"].get("receipts_written", 0))

        processed = 0
        completed_ok = 0
        failed = 0
        for q in pending:
            if _STOP["requested"]:
                log.warning("stop requested; %s question(s) left for the next invocation", len(pending) - processed)
                break
            per_arm = []
            for agent in agents:
                res = agent.run(q)
                per_arm.append(res.to_dict())
                if res.error:
                    log.error("question %s arm %s errored: %s", q["id"], agent.agent_id, res.error)

            # --- execution-failure semantics (defect #9 fix, 2026-08-20) ---
            # An arm error means the provider call chain failed: the model did
            # NOT complete the task, so nothing substantive exists to record.
            # Different from "completed and proposed zero citations" and from
            # "proposed citations, all failed the gate" - both of which produce
            # clean arms with NO error. A failed question is never marked
            # complete, emits no receipt, and stays pending for retry.
            arm_errors = [a for a in per_arm if a.get("error")]
            if arm_errors:
                failed += 1
                processed += 1
                fails = state.data.setdefault("execution_failures", {})
                entry = fails.setdefault(q["id"], {"attempts": 0, "history": []})
                entry["attempts"] += 1
                entry["history"].append({
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "invocation_model": meta["model"],
                    "arms": [{"agent": a.get("agent", ""),
                              "category": str(a.get("error", "")).split(":", 1)[0],
                              "error": str(a.get("error", ""))[:500]} for a in arm_errors],
                })
                append_jsonl(paths["results"], {
                    "schema": "experiment-a/execution-error/1",
                    "status": "provider_error",
                    "run_id": run_id,
                    "harness_version": HARNESS_VERSION,
                    "question_set_digest": digest,
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "question_id": q["id"], "domain": q["domain"],
                    "model": meta["model"], "attempt": entry["attempts"],
                    "arms": per_arm,
                    "note": "Execution failure: not a substantive outcome; question remains pending and is retried on rerun.",
                })
                state.save()
                log.error("%s NOT completed: execution failure in %s arm(s) (attempt %s); stays pending",
                          q["id"], len(arm_errors), entry["attempts"])
                if args.delay_ms:
                    time.sleep(args.delay_ms / 1000.0)
                continue

            record = {
                "schema": "experiment-a/raw/1",
                "run_id": run_id,
                "harness_version": HARNESS_VERSION,
                "question_set_digest": digest,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "question": {
                    "id": q["id"],
                    "domain": q["domain"],
                    "question": q["question"],
                    "citation_expectation": q.get("citation_expectation", ""),
                },
                "model": meta["model"],
                "dry_run": bool(args.dry_run),
                "arms": per_arm,
                "note": (
                    "Ground-truth labels are produced by the independent deterministic "
                    "stack (docs/ground-truth-spec.md) and joined on citation_id; no label "
                    "is written by this harness and no /evaluate output is used as a label."
                ),
            }
            # Order matters for resumability: the results line is durable before
            # the question is marked complete, so a crash can duplicate nothing.
            append_jsonl(paths["results"], record)
            state.record(q["id"], per_arm, receipt_baseline + receipts.count)
            state.save()
            processed += 1
            completed_ok += 1
            log.info(
                "%s done (%s/%s in this invocation; %s complete overall)",
                q["id"], processed, len(pending), len(state.completed),
            )
            if args.delay_ms:
                time.sleep(args.delay_ms / 1000.0)

        c = state.data["counts"]
        log.info(
            "invocation complete: processed=%s completed=%s failed=%s | cumulative questions_run=%s citations_emitted=%s "
            "citations_blocked=%s gate_saw=%s replans=%s receipts=%s",
            processed, completed_ok, failed, c["questions_run"], c["citations_emitted"], c["citations_blocked"],
            c["citations_gate_saw"], c["replans"], c["receipts_written"],
        )
        if failed and not completed_ok:
            log.error("every question hit an execution failure; nothing marked complete "
                      "and nothing substantive recorded. Exit %s.", EXIT_ALL_FAILED)
            return EXIT_ALL_FAILED
        if failed:
            log.error("%s question(s) hit execution failures and remain pending; "
                      "rerun to retry. Exit %s.", failed, EXIT_PARTIAL)
            return EXIT_PARTIAL
        return EXIT_OK
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
