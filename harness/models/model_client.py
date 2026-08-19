"""Base-model adapters for the two agents.

The same model drives Agent U and Agent G within a run — the gate is the only
variable (``docs/pre-registration.md``, "Design"). This module resolves a model
id from ``model_config.yaml``, checks the pinning rule, and returns a client.

Two client kinds:

* :class:`FixtureModelClient` — used by ``--dry-run``. Deterministic, offline,
  no API key, no network, no tokens spent. It exercises the full agent loop
  (plan -> gather -> propose citations -> finalize, including replans) with
  synthetic candidate citations derived from a stable hash of the question.
* :class:`LiveModelClient` — the live path. It is intentionally inert until the
  pre-registered collection window opens: constructing it requires an explicit
  provider API key and a resolved version pin, and it refuses to run if the pin
  is missing, per the model-independence selection rule ("a model that cannot
  be pinned for the duration cannot carry a pre-registered result").

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

CONFIG_PATH = pathlib.Path(__file__).with_name("model_config.yaml")


class ModelConfigError(RuntimeError):
    pass


@dataclass
class ModelSpec:
    family: str
    model: str
    pin: Optional[str] = None

    @property
    def pinned(self) -> bool:
        return bool(self.pin)

    @property
    def label(self) -> str:
        return f"{self.model}@{self.pin}" if self.pin else self.model


def load_model_specs(path: pathlib.Path = CONFIG_PATH) -> List[ModelSpec]:
    if yaml is None:
        raise ModelConfigError("PyYAML is required to read model_config.yaml")
    doc = yaml.safe_load(path.read_text()) or {}
    specs = [ModelSpec(**{k: m.get(k) for k in ("family", "model", "pin")}) for m in doc.get("models", [])]
    if not specs:
        raise ModelConfigError(f"no models declared in {path}")
    return specs


def resolve_model(name: str, path: pathlib.Path = CONFIG_PATH) -> ModelSpec:
    specs = load_model_specs(path)
    for s in specs:
        if name in (s.model, s.label, f"{s.family}/{s.model}"):
            return s
    known = ", ".join(s.model for s in specs)
    raise ModelConfigError(f"unknown model {name!r}; declared models: {known}")


# --------------------------------------------------------------------- clients
@dataclass
class Candidate:
    """A candidate citation proposed by the model during research."""

    url: str
    title: str
    doi: str = ""
    locator: str = ""
    claim: str = ""
    provenance: str = "model_proposed"

    def as_citation(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "doi": self.doi,
            "locator": self.locator,
            "provenance": self.provenance,
        }


class BaseModelClient:
    """Interface the agents depend on. Deliberately tiny."""

    name = "base"
    dry_run = False

    def plan(self, question: Dict[str, Any]) -> List[str]:
        raise NotImplementedError

    def propose_citations(
        self, question: Dict[str, Any], step: str, attempt: int, n: int = 2
    ) -> List[Candidate]:
        raise NotImplementedError

    def finalize(self, question: Dict[str, Any], citations: List[Dict[str, Any]]) -> str:
        raise NotImplementedError


class FixtureModelClient(BaseModelClient):
    """Offline deterministic stand-in used by ``--dry-run``.

    Given the same question id and attempt number it always returns the same
    candidates, so a dry run is reproducible byte-for-byte and the resume path
    can be checked for double counting.
    """

    dry_run = True

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.name = spec.label
        self.calls: Dict[str, int] = {"plan": 0, "propose": 0, "finalize": 0}

    # -- helpers
    @staticmethod
    def _h(*parts: str) -> int:
        return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")

    def plan(self, question: Dict[str, Any]) -> List[str]:
        self.calls["plan"] += 1
        return [
            "identify the authoritative primary source",
            "retrieve the passage that states the claim",
            "check whether a second independent source corroborates it",
        ]

    def propose_citations(
        self, question: Dict[str, Any], step: str, attempt: int, n: int = 2
    ) -> List[Candidate]:
        self.calls["propose"] += 1
        qid = question["id"]
        out: List[Candidate] = []
        for i in range(n):
            seed = self._h(self.name, qid, step, str(attempt), str(i))
            host = ["example.org", "example.net", "example.com"][seed % 3]
            out.append(
                Candidate(
                    url=f"https://{host}/fixture/{qid}/a{attempt}/s{seed % 9973}",
                    title=f"Fixture source {seed % 9973} for {qid}",
                    doi=f"10.5555/fixture.{seed % 99991}" if seed % 3 == 0 else "",
                    locator=f"section {1 + seed % 12}",
                    claim=f"[dry-run] proposed support for {qid} via '{step}'",
                )
            )
        return out

    def finalize(self, question: Dict[str, Any], citations: List[Dict[str, Any]]) -> str:
        self.calls["finalize"] += 1
        return (
            f"[dry-run answer for {question['id']}] synthesized from "
            f"{len(citations)} emitted citation(s). No live model was called."
        )


# Provider aliases that must never appear as a pin. Each value explains what the
# alias resolves to, so the error message is actionable rather than cryptic.
_PROHIBITED_ALIASES: Dict[str, str] = {
    "gpt-5.6": "It routes to gpt-5.6-sol and can be repointed by the provider.",
    "gpt-5.6-chat-latest": "A -latest channel; the provider updates it regularly.",
    "mistral-medium-latest": "Resolves to the latest GA version across generations.",
    "mistral-medium-3": "Resolves to the latest minor version of that major generation.",
    "claude-sonnet-4-5": "A convenience alias to the most recent dated snapshot.",
    "kimi-latest": "A moving pointer; discontinued 2026-01-28.",
}


@dataclass
class LiveModelClient(BaseModelClient):
    """Live provider path. Refuses to operate without a pin and a key."""

    spec: ModelSpec
    api_key: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = self.spec.label
        if not self.spec.pinned:
            raise ModelConfigError(
                f"model {self.spec.model!r} has no version pin in model_config.yaml; "
                "the pre-registration requires exact pins before the first live run "
                "(docs/model-independence.md, selection rule). Use --dry-run until pins land."
            )
        # Aliases are prohibited by the selection rule: a provider can repoint
        # an alias mid-window, which is the failure the rule exists to exclude.
        # Refuse rather than warn, so a misconfigured alias cannot silently
        # carry a pre-registered result.
        if self.spec.pin in _PROHIBITED_ALIASES:
            raise ModelConfigError(
                f"{self.spec.pin!r} is a provider ALIAS, not a pinned snapshot. "
                f"{_PROHIBITED_ALIASES[self.spec.pin]} "
                "The pre-registration selection rule requires an exact pin "
                "(docs/pre-registration.md). Refusing to run."
            )
        env_var = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }.get(self.spec.family, "MODEL_API_KEY")
        self.api_key = self.api_key or os.environ.get(env_var, "")
        if not self.api_key:
            raise ModelConfigError(f"{env_var} is not set; cannot run live model {self.spec.model}")

    def plan(self, question: Dict[str, Any]) -> List[str]:  # pragma: no cover - live path
        raise NotImplementedError(
            "Live provider calls are enabled at the start of the pre-registered "
            "collection window (2026-08-19). Wire the provider SDK here."
        )

    propose_citations = plan  # type: ignore[assignment]
    finalize = plan  # type: ignore[assignment]


def get_model_client(name: str, dry_run: bool, config_path: pathlib.Path = CONFIG_PATH) -> BaseModelClient:
    spec = resolve_model(name, config_path)
    if dry_run:
        return FixtureModelClient(spec)
    return LiveModelClient(spec=spec)


__all__ = [
    "BaseModelClient",
    "Candidate",
    "FixtureModelClient",
    "LiveModelClient",
    "ModelConfigError",
    "ModelSpec",
    "get_model_client",
    "load_model_specs",
    "resolve_model",
]
