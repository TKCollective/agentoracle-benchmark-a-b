"""Client for the AgentOracle ``/evaluate`` gate.

Scope note (binding, per ``docs/pre-registration.md``): ``/evaluate`` is the
*intervention under test*, never the scorer. Nothing in this module produces a
ground-truth label; it only records the gate's verdict so the verdict can later
be compared against independently computed labels.

Three outcomes are modelled, and only one of them passes:

* ``valid``          -> passes the gate
* ``invalid``        -> does not pass
* ``indeterminate``  -> does NOT pass (explicitly; an unverifiable citation is
  not a verified citation)

Transport failures raise / surface as ``GateError`` outcomes. A gate error can
never silently become a pass: ``GateDecision.passed`` is ``True`` only for the
literal ``valid`` verdict, and the error path returns ``outcome="error"`` with
``passed == False``.

The *fail-closed replan* behaviour deliberately does NOT live here. This client
reports; the integrator (``harness/agents/agent_gated.py``) decides. Keeping the
policy out of the client means the client cannot be the place a policy bug
turns a failure into a shipped citation.

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

try:  # requests is pinned in requirements.txt; dry runs must work without it.
    import requests
except Exception:  # pragma: no cover - offline/dry-run environments
    requests = None  # type: ignore

log = logging.getLogger(__name__)

# The only verdict string that is allowed to pass the gate.
VERDICT_VALID = "valid"
VERDICT_INVALID = "invalid"
VERDICT_INDETERMINATE = "indeterminate"

PASSING_VERDICTS = frozenset({VERDICT_VALID})
KNOWN_VERDICTS = frozenset({VERDICT_VALID, VERDICT_INVALID, VERDICT_INDETERMINATE})

# Corrected 2026-08-20 (dated deviation): api.agentoracle.co has no DNS record.
DEFAULT_ENDPOINT = "https://agentoracle.co/evaluate"

#: Sent with every request; the gate's claim-level verdicts (not this
#: threshold) drive the three-way mapping below. Deviation 2026-08-20c.
GATE_MIN_CONFIDENCE = 0.8


class GateError(RuntimeError):
    """Raised for transport/protocol failures talking to the gate."""


@dataclass
class GateDecision:
    """One gate decision about one proposed citation.

    ``outcome`` is one of ``valid`` / ``invalid`` / ``indeterminate`` / ``error``.
    ``passed`` is derived, never supplied by the caller or by the server.
    """

    citation_id: str
    question_id: str
    outcome: str
    reason: str = ""
    attempts: int = 1
    latency_ms: int = 0
    http_status: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    endpoint: str = DEFAULT_ENDPOINT
    dry_run: bool = False

    @property
    def passed(self) -> bool:
        """True only for an explicit ``valid`` verdict.

        ``indeterminate`` and ``error`` are not passes. This is the single
        place the notion of "passing" is defined.
        """
        return self.outcome in PASSING_VERDICTS

    @property
    def is_error(self) -> bool:
        return self.outcome == "error"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


class EvaluateClient:
    """HTTP client for ``/evaluate`` with timeouts, retries and backoff.

    Parameters
    ----------
    endpoint:
        Full URL of the ``/evaluate`` endpoint. Defaults to the value of
        ``AGENTORACLE_EVALUATE_URL`` or :data:`DEFAULT_ENDPOINT`.
    api_key:
        Bearer token; read from ``AGENTORACLE_API_KEY`` when omitted.
    dry_run:
        When true, no network call is made at all. Verdicts come from a
        deterministic local fixture keyed by citation content, so a dry run
        exercises every branch (valid / invalid / indeterminate / error)
        reproducibly and without touching the live gate.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 20.0,
        max_attempts: int = 4,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        dry_run: bool = False,
        session: Any = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("AGENTORACLE_EVALUATE_URL", DEFAULT_ENDPOINT)
        self.api_key = api_key or os.environ.get("AGENTORACLE_API_KEY", "")
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.dry_run = bool(dry_run)
        self._session = session
        if not self.dry_run and self._session is None:
            if requests is None:
                raise GateError("requests is required for live gate calls")
            self._session = requests.Session()

    # ------------------------------------------------------------------ public
    def evaluate(
        self,
        *,
        question_id: str,
        citation_id: str,
        claim: str,
        citation: Dict[str, Any],
        domain: str = "",
        model: str = "",
    ) -> GateDecision:
        """Submit one proposed citation to the gate and return its decision.

        Never raises for a gate-side failure: the failure is returned as an
        ``error`` outcome, which does not pass. Callers are therefore unable to
        accidentally treat an exception-swallowed failure as success.
        """
        # Production /evaluate contract (probed live 2026-08-20, deviation
        # 2026-08-20c): {"content": <claim text>, "min_confidence": <float>}.
        # question_id / citation_id / citation stay local - they identify the
        # decision in OUR receipts; the gate verifies the claim itself.
        payload = {
            "content": claim,
            "min_confidence": GATE_MIN_CONFIDENCE,
        }

        if self.dry_run:
            # Fixture decisions keep the original six-field blob so dry-run
            # behaviour is byte-identical to the pre-adapter harness.
            return self._fixture_decision({
                "question_id": question_id,
                "citation_id": citation_id,
                "domain": domain,
                "model": model,
                "claim": claim,
                "citation": citation,
            })

        started = time.time()
        last_err = ""
        status: Optional[int] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._session.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout,
                    headers=self._headers(),
                )
                status = resp.status_code
                if status in (429, 500, 502, 503, 504):
                    last_err = f"retryable http {status}"
                    self._sleep(attempt)
                    continue
                if status >= 400:
                    # Non-retryable protocol error: the gate did not evaluate.
                    # Raised, not returned as a verdict - a gate that could not
                    # answer is an execution failure, never a "fail" decision
                    # (deviation 2026-08-20c, same family as defect #9).
                    raise GateError(f"gate http {status}: {resp.text[:300]}")
                body = resp.json()
                verdict = self._normalise_verdict(body)
                return GateDecision(
                    citation_id=citation_id,
                    question_id=question_id,
                    outcome=verdict,
                    reason=str(((body.get("evaluation") or {}).get("recommendation_text"))
                               or body.get("reason", ""))[:2000],
                    attempts=attempt,
                    latency_ms=int((time.time() - started) * 1000),
                    http_status=status,
                    raw=body if isinstance(body, dict) else {"body": body},
                    endpoint=self.endpoint,
                )
            except GateError:
                raise
            except Exception as exc:  # network error, timeout, bad JSON
                last_err = f"{type(exc).__name__}: {exc}"
                log.warning("gate call failed (attempt %s/%s): %s", attempt, self.max_attempts, last_err)
                if attempt < self.max_attempts:
                    self._sleep(attempt)

        # Exhausted retries: the gate never evaluated. Execution failure,
        # not a verdict (deviation 2026-08-20c).
        raise GateError(last_err or "gate unreachable after retries")

    # ----------------------------------------------------------------- helpers
    def _headers(self) -> Dict[str, str]:
        h = {"content-type": "application/json", "accept": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def _sleep(self, attempt: int) -> None:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
        delay += random.uniform(0, delay * 0.25)  # jitter
        time.sleep(delay)

    @staticmethod
    @staticmethod
    def _normalise_verdict(body: Any) -> str:
        """Map the production /evaluate response onto the three known verdicts.

        Contract probed live 2026-08-20 (deviation 2026-08-20c): the response
        carries ``evaluation.claims[]`` with claim-level verdicts
        ``supported`` / ``refuted`` / ``unverifiable``. Mapping: any refuted
        claim -> invalid; all claims supported -> valid; anything else
        (unverifiable present, empty, unknown) -> indeterminate. Falls back to
        ``evaluation.recommendation`` (act -> valid, reject/halt -> invalid),
        then to legacy flat keys, then indeterminate. Unknown vocabulary can
        only weaken toward indeterminate - never toward a pass.
        """
        if not isinstance(body, dict):
            return VERDICT_INDETERMINATE
        ev = body.get("evaluation") or {}
        claims = ev.get("claims") or []
        if isinstance(claims, list) and claims:
            vs = [str(c.get("verdict", "")).strip().lower() for c in claims if isinstance(c, dict)]
            if any(v == "refuted" for v in vs):
                return VERDICT_INVALID
            if vs and all(v == "supported" for v in vs):
                return VERDICT_VALID
            return VERDICT_INDETERMINATE
        rec = str(ev.get("recommendation", body.get("verdict", ""))).strip().lower()
        if rec == "act":
            return VERDICT_VALID
        if rec in ("reject", "halt"):
            return VERDICT_INVALID
        raw = str(body.get("verdict", body.get("result", body.get("status", "")))).strip().lower()
        if raw in ("valid", "supported", "pass", "passed", "true"):
            return VERDICT_VALID
        if raw in ("invalid", "refuted", "fail", "failed", "false"):
            return VERDICT_INVALID
        return VERDICT_INDETERMINATE

    def _fixture_decision(self, payload: Dict[str, Any]) -> GateDecision:
        """Deterministic offline verdict for ``--dry-run``.

        Derived from a stable hash of the request so repeated dry runs are
        byte-identical, and so all four branches occur in a 5-question run.
        No network I/O, no live gate, no LLM.
        """
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        bucket = hashlib.sha256(blob).digest()[0] % 20
        if bucket < 11:
            outcome, reason = VERDICT_VALID, "fixture: claim located in cited source"
        elif bucket < 16:
            outcome, reason = VERDICT_INVALID, "fixture: cited source does not contain the claim"
        elif bucket < 19:
            outcome, reason = VERDICT_INDETERMINATE, "fixture: source not retrievable"
        else:
            outcome, reason = "error", "fixture: simulated gate transport failure"
        return GateDecision(
            citation_id=payload["citation_id"],
            question_id=payload["question_id"],
            outcome=outcome,
            reason=reason,
            attempts=1,
            latency_ms=0,
            http_status=None if outcome == "error" else 200,
            raw={"fixture": True, "bucket": bucket},
            endpoint="dry-run://fixture",
            dry_run=True,
        )


__all__ = [
    "EvaluateClient",
    "GateDecision",
    "GateError",
    "VERDICT_VALID",
    "VERDICT_INVALID",
    "VERDICT_INDETERMINATE",
    "PASSING_VERDICTS",
]
