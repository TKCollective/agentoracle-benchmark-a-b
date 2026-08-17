"""Agent U — the ungated deep-research agent.

Agent U runs the normal research loop with **no verification gate**: every
citation it proposes is emitted. It is the control arm.

The loop lives here and is reused verbatim by Agent G
(``agent_gated.py``), which subclasses this class and overrides exactly one
hook — ``vet_candidate`` — so the two arms are structurally identical except
for the gate. That is the one-variable requirement of the design
(``docs/pre-registration.md``, "Design").

No ground-truth labelling happens in this module. Emitted and (for Agent G)
blocked citations are recorded so the independent deterministic layer can
label them later; the harness never grades its own citations.

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.models.model_client import BaseModelClient, Candidate

AGENT_UNGATED = "U"


def citation_id(question_id: str, url: str, doi: str, attempt: int, index: int) -> str:
    """Stable id for one proposed citation, used to join gate verdicts,
    receipts, and ground-truth labels across files."""
    h = hashlib.sha256(f"{question_id}|{url}|{doi}|{attempt}|{index}".encode()).hexdigest()[:16]
    return f"{question_id}-c{h}"


@dataclass
class VetResult:
    """Outcome of vetting one candidate before it may be emitted."""

    emit: bool
    gate_seen: bool = False
    outcome: str = "not_gated"
    reason: str = ""
    decision: Any = None


@dataclass
class AgentResult:
    """Everything one question produced under one agent arm."""

    question_id: str
    domain: str
    agent: str
    model: str
    answer: str = ""
    emitted_citations: List[Dict[str, Any]] = field(default_factory=list)
    blocked_citations: List[Dict[str, Any]] = field(default_factory=list)
    gate_decisions: List[Dict[str, Any]] = field(default_factory=list)
    replans: int = 0
    receipts_written: int = 0
    duration_ms: int = 0
    dry_run: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "domain": self.domain,
            "agent": self.agent,
            "model": self.model,
            "answer": self.answer,
            "emitted_citations": self.emitted_citations,
            "blocked_citations": self.blocked_citations,
            "gate_decisions": self.gate_decisions,
            "counts": {
                "citations_emitted": len(self.emitted_citations),
                "citations_blocked": len(self.blocked_citations),
                "citations_gate_saw": len(self.gate_decisions),
                "replans": self.replans,
                "receipts_written": self.receipts_written,
            },
            "duration_ms": self.duration_ms,
            "dry_run": self.dry_run,
            "error": self.error,
        }


class UngatedAgent:
    """Agent U. Also the shared research loop for both arms.

    Parameters
    ----------
    model:
        A :class:`~harness.models.model_client.BaseModelClient`.
    citations_per_step:
        How many candidate citations the model proposes per research step.
    max_replans:
        Number of extra research attempts per step. Agent U never replans for
        gate reasons (it has no gate), so this is 0 for the control arm.
    """

    agent_id = AGENT_UNGATED
    name = "agent_ungated"

    def __init__(
        self,
        model: BaseModelClient,
        citations_per_step: int = 2,
        max_replans: int = 0,
        receipt_writer: Any = None,
    ) -> None:
        self.model = model
        self.citations_per_step = citations_per_step
        self.max_replans = max_replans
        self.receipts = receipt_writer

    # ------------------------------------------------------------------ hooks
    def vet_candidate(
        self,
        question: Dict[str, Any],
        candidate: Candidate,
        cid: str,
        attempt: int,
    ) -> VetResult:
        """Ungated: everything the model proposes is emitted."""
        return VetResult(emit=True, gate_seen=False, outcome="not_gated")

    def on_step_complete(self, result: AgentResult, step: str, attempt: int, emitted_any: bool) -> None:
        """Hook for arms that need to react to a step's outcome."""
        return None

    # ------------------------------------------------------------------- loop
    def run(self, question: Dict[str, Any]) -> AgentResult:
        started = time.time()
        result = AgentResult(
            question_id=question["id"],
            domain=question.get("domain", ""),
            agent=self.agent_id,
            model=getattr(self.model, "name", "unknown"),
            dry_run=bool(getattr(self.model, "dry_run", False)),
        )
        try:
            plan = self.model.plan(question)
            for step in plan:
                attempt = 1
                while True:
                    emitted_any = False
                    candidates = self.model.propose_citations(
                        question, step, attempt, n=self.citations_per_step
                    )
                    for index, cand in enumerate(candidates):
                        cid = citation_id(question["id"], cand.url, cand.doi, attempt, index)
                        vet = self.vet_candidate(question, cand, cid, attempt)
                        record = {
                            "citation_id": cid,
                            "question_id": question["id"],
                            "attempt": attempt,
                            "step": step,
                            "claim": cand.claim,
                            **cand.as_citation(),
                        }
                        if vet.gate_seen:
                            record["gate_outcome"] = vet.outcome
                            result.gate_decisions.append(
                                {
                                    "citation_id": cid,
                                    "outcome": vet.outcome,
                                    "passed": bool(vet.emit),
                                    "reason": vet.reason,
                                    "attempt": attempt,
                                }
                            )
                        if vet.emit:
                            result.emitted_citations.append(record)
                            emitted_any = True
                        elif vet.gate_seen:
                            # Blocked citations are retained: the ground-truth
                            # spec requires labelling every citation the gate
                            # saw, including the ones it rejected.
                            result.blocked_citations.append(record)
                    self.on_step_complete(result, step, attempt, emitted_any)
                    if emitted_any or attempt > self.max_replans:
                        break
                    attempt += 1
                    result.replans += 1
                    self._emit_replan_receipt(question, step, attempt)
            result.answer = self.model.finalize(question, result.emitted_citations)
        except Exception as exc:  # a failed question is recorded, never silently dropped
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_ms = int((time.time() - started) * 1000)
        if self.receipts is not None:
            result.receipts_written = getattr(self.receipts, "count", 0)
        return result

    # ---------------------------------------------------------------- helpers
    def _emit_replan_receipt(self, question: Dict[str, Any], step: str, attempt: int) -> None:
        """Agent U has no gate decisions, so it emits no receipts."""
        return None


__all__ = ["UngatedAgent", "AgentResult", "VetResult", "citation_id", "AGENT_UNGATED"]
