"""Agent G — the gated deep-research agent.

Identical loop to Agent U: this class subclasses :class:`UngatedAgent` and does
not reimplement the research loop. It overrides exactly one hook,
``vet_candidate``, so the only difference between the two arms is the gate.

**Fail-closed policy lives here, in the integrator, not in the gate client.**
A citation is emitted only on an explicit ``valid`` verdict. Anything else —
``invalid``, ``indeterminate``, or a gate transport error — blocks the citation
and, if no citation survives the step, triggers a replan. The gate client
merely reports; the decision to withhold is made in this module, which is the
only place that decides what ships.

Receipts: every gate decision emits a signed verification-state receipt —
``pass``, ``fail``, and ``replan`` — per ``docs/pre-registration.md``.

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from harness.agents.agent_ungated import AgentResult, UngatedAgent, VetResult
from harness.gate.evaluate_client import EvaluateClient, GateDecision
from harness.models.model_client import BaseModelClient, Candidate
from harness.receipts.receipt_writer import (
    DECISION_FAIL,
    DECISION_PASS,
    DECISION_REPLAN,
)

AGENT_GATED = "G"


class GatedAgent(UngatedAgent):
    """Agent G: Agent U's loop plus a fail-closed pre-emission gate."""

    agent_id = AGENT_GATED
    name = "agent_gated"

    def __init__(
        self,
        model: BaseModelClient,
        gate: EvaluateClient,
        citations_per_step: int = 2,
        max_replans: int = 2,
        receipt_writer: Any = None,
    ) -> None:
        super().__init__(
            model=model,
            citations_per_step=citations_per_step,
            max_replans=max_replans,
            receipt_writer=receipt_writer,
        )
        self.gate = gate
        self._current_question: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ hooks
    def vet_candidate(
        self,
        question: Dict[str, Any],
        candidate: Candidate,
        cid: str,
        attempt: int,
    ) -> VetResult:
        self._current_question = question
        decision: GateDecision = self.gate.evaluate(
            question_id=question["id"],
            citation_id=cid,
            claim=candidate.claim or question["question"],
            citation=candidate.as_citation(),
            domain=question.get("domain", ""),
            model=getattr(self.model, "name", ""),
        )

        # ---- FAIL-CLOSED DECISION (the one line of policy in this experiment)
        # Emit only on an explicit pass. invalid / indeterminate / error all
        # withhold. `decision.passed` is True solely for verdict == "valid".
        emit = bool(decision.passed)

        self._emit_gate_receipt(
            question=question,
            candidate=candidate,
            cid=cid,
            attempt=attempt,
            decision=decision,
            decision_class=DECISION_PASS if emit else DECISION_FAIL,
        )

        return VetResult(
            emit=emit,
            gate_seen=True,
            outcome=decision.outcome,
            reason=decision.reason,
            decision=decision.to_dict(),
        )

    # --------------------------------------------------------------- receipts
    def _emit_gate_receipt(
        self,
        *,
        question: Dict[str, Any],
        candidate: Candidate,
        cid: str,
        attempt: int,
        decision: GateDecision,
        decision_class: str,
    ) -> None:
        if self.receipts is None:
            return
        citation = candidate.as_citation()
        citation["_gate_endpoint"] = decision.endpoint
        self.receipts.emit(
            decision_class=decision_class,
            question_id=question["id"],
            domain=question.get("domain", ""),
            model=getattr(self.model, "name", ""),
            agent=self.agent_id,
            citation_id=cid,
            citation=citation,
            claim=candidate.claim or question["question"],
            gate_outcome=decision.outcome,
            gate_passed=decision.passed,
            gate_reason=decision.reason,
            attempt=attempt,
            extra={"gate_attempts": decision.attempts, "gate_latency_ms": decision.latency_ms},
        )

    def _emit_replan_receipt(self, question: Dict[str, Any], step: str, attempt: int) -> None:
        """Replan is itself a gate decision class and gets its own receipt."""
        if self.receipts is None:
            return
        self.receipts.emit(
            decision_class=DECISION_REPLAN,
            question_id=question["id"],
            domain=question.get("domain", ""),
            model=getattr(self.model, "name", ""),
            agent=self.agent_id,
            citation_id=f"{question['id']}-replan{attempt}",
            citation={"url": "", "doi": "", "title": "", "locator": step, "_gate_endpoint": self.gate.endpoint},
            claim=question["question"],
            gate_outcome="replan",
            gate_passed=False,
            gate_reason=(
                "no proposed citation passed the gate for this research step; "
                "agent replans rather than emitting an unverified citation"
            ),
            attempt=attempt,
        )


__all__ = ["GatedAgent", "AgentResult", "AGENT_GATED"]
