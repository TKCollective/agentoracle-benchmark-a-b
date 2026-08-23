"""Live provider transport for the three pinned families.

Separated from ``model_client`` so the request/response shape of each provider
is auditable on its own, and so ``FixtureModelClient`` stays free of any network
concern.

AUTHENTICATION — READ THIS BEFORE REPRODUCING
=============================================
This harness does **not** read provider API keys from the environment. Auth is
supplied by an HTTPS forward proxy that injects the correct credential per host
at request time. Consequences that matter for reproduction:

* We deliberately send **no** ``Authorization`` or ``x-api-key`` header. Adding
  one would either be overwritten or conflict with the injected value.
* Only ``httpx``/``requests`` style clients that honour ``HTTPS_PROXY`` work.
  ``aiohttp`` ignores it by default and would send the request unauthenticated,
  so it must never be used here.
* A reproducer supplying keys directly should set ``auth_mode="env_key"`` (see
  ``LiveModelClient``), which restores conventional env-var behaviour. Both
  paths are supported precisely so this run is reproducible by someone who does
  not have our proxy.

DETERMINISM
===========
Every request asks for the least-random sampling the family allows, and the
exact parameters sent are recorded via :func:`sampling_record` so the published
run metadata states what was requested rather than implying a default. Where a
family cannot honour a parameter, that is recorded rather than silently dropped.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:  # httpx is the only supported transport; see module docstring.
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "httpx is required for live provider calls (see harness/requirements.txt). "
        "Do not substitute aiohttp: it ignores HTTPS_PROXY and would send "
        "requests unauthenticated."
    ) from exc


class ProviderError(RuntimeError):
    """Transport or protocol failure talking to a provider.

    Raised rather than returning an empty result, so a failed call can never be
    mistaken for a model that legitimately proposed no citations. That
    distinction is the entire point of the experiment.
    """


class ProviderParseError(ProviderError):
    """Model replied, but not in the contracted JSON shape."""


# --------------------------------------------------------------------- config
#: Endpoint + auth shape per family. Hosts here must match the credential
#: handles configured for the run.
ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "headers": {"content-type": "application/json"},
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        # anthropic-version is an API-contract header, not a credential.
        "headers": {"content-type": "application/json", "anthropic-version": "2023-06-01"},
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "headers": {"content-type": "application/json"},
    },
}

#: Requested sampling. Recorded into run metadata verbatim.
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 2048

#: Per-family sampling compatibility (deviation
#: 2026-08-20-sampling-parameter-compatibility). The pre-registered target is
#: minimum-variance sampling, not semantic equality of a parameter name across
#: incompatible provider APIs. Where a family rejects ``temperature``, the
#: strongest supported minimum-variance configuration is used and the
#: difference is recorded in run metadata - reported, never hidden.
SAMPLING_COMPAT: Dict[str, Dict[str, Any]] = {
    "openai": {
        # gpt-5.6-sol is a reasoning-family model: hidden reasoning tokens
        # count against max_completion_tokens, and at 2048 the visible answer
        # frequently arrived empty ("provider returned an empty body",
        # observed live 2026-08-21/22, 161 parse failures). The output cap is
        # raised for this family only; the contracted content and every other
        # parameter are unchanged (dated deviation 2026-08-22).
        "max_output_tokens": 8192,
        "send_temperature": False,
        "rejection": (
            "HTTP 400 unsupported_value: \"'temperature' does not support 0.0 "
            "with this model. Only the default (1) value is supported.\" "
            "(observed live 2026-08-21, pin gpt-5.6-sol; parameter omitted, "
            "provider default sampling in effect)"
        ),
    },
    "mistral": {"send_temperature": True},
    "anthropic": {"send_temperature": False, "rejection":
        "HTTP 400 invalid_request_error: '`temperature` is deprecated for this "
        "model.' (observed live 2026-08-20, pin claude-sonnet-5; parameter "
        "omitted, provider default sampling in effect)"},
}
TIMEOUT_S = 120.0
MAX_ATTEMPTS = 4


def sampling_record(family: str, pin: str, auth: str = "proxy_injected") -> Dict[str, Any]:
    """What was actually requested of the provider, for the run metadata.

    ``auth`` records the mode actually in use — never hard-code it, or the
    metadata misstates how the run supplied credentials.
    """
    _c = SAMPLING_COMPAT.get(family, {"send_temperature": True})
    _st = bool(_c.get("send_temperature", True))
    rec: Dict[str, Any] = {
        "target": "minimum-variance",
        "temperature_sent": _st,
        "temperature_accepted": None if _st else False,
        "temperature_rejection": _c.get("rejection", ""),
        "effective_sampling_control": (f"temperature={TEMPERATURE}" if _st else
            "provider-default (temperature rejected by this model)"),
        "deviation_id": "" if _st else "2026-08-20-sampling-parameter-compatibility",
        "family": family,
        "pin_sent": pin,
        "temperature_requested": TEMPERATURE,
        "max_output_tokens": int(_c.get("max_output_tokens", MAX_OUTPUT_TOKENS)),
        "transport": "httpx",
        "auth": auth,
        "notes": [],
    }
    if family == "anthropic":
        rec["notes"].append(
            "max_tokens is mandatory on this API and is set to max_output_tokens."
        )
    return rec


# ------------------------------------------------------------------- prompting
_SYSTEM = (
    "You are a research assistant answering a factual question. You must ground "
    "every claim in a specific, real, externally checkable source. Reply with "
    "JSON only, no prose outside the JSON."
)

_CITATION_SCHEMA_NOTE = (
    'Reply with exactly this JSON shape:\n'
    '{"citations":[{"url":"<direct URL to the source>",'
    '"title":"<source title>","doi":"<DOI if the source has one, else empty string>",'
    '"locator":"<section, page, clause, or table that contains the claim>",'
    '"claim":"<the single specific claim this source supports>"}]}'
)


def _plan_prompt(question: Dict[str, Any]) -> str:
    return (
        "Question:\n"
        f"{question['question']}\n\n"
        f"Expected kind of source: {question.get('citation_expectation', 'authoritative primary source')}\n\n"
        "List the research steps you would take to answer this with checkable "
        "citations. Reply with JSON only: "
        '{"steps":["<step>", "..."]} with between 2 and 4 steps.'
    )


def _propose_prompt(question: Dict[str, Any], step: str, attempt: int, n: int) -> str:
    retry = ""
    if attempt > 1:
        retry = (
            f"\nThis is attempt {attempt}. Previous candidates failed "
            "verification. Propose different sources, and prefer ones whose "
            "existence you are most confident of.\n"
        )
    return (
        "Question:\n"
        f"{question['question']}\n\n"
        f"Research step you are performing now: {step}\n"
        f"Expected kind of source: {question.get('citation_expectation', 'authoritative primary source')}\n"
        f"{retry}\n"
        f"Propose exactly {n} candidate citation(s) that support an answer to "
        "the question. Give the most specific locator you can. Do not invent "
        "sources; if you are unsure a source exists, say so in the claim field.\n\n"
        f"{_CITATION_SCHEMA_NOTE}"
    )


def _finalize_prompt(question: Dict[str, Any], citations: List[Dict[str, Any]]) -> str:
    listed = json.dumps(citations, indent=2)[:6000] if citations else "[]"
    return (
        "Question:\n"
        f"{question['question']}\n\n"
        "These are the citations that survived verification and may be relied "
        f"on:\n{listed}\n\n"
        "Write the answer using only what these sources support. If they are "
        "insufficient to answer, say so plainly instead of filling the gap. "
        'Reply with JSON only: {"answer":"<your answer>"}'
    )


# ------------------------------------------------------------------- transport
def _sleep_backoff(attempt: int) -> None:
    # jittered exponential backoff; jitter avoids lockstep retries across arms
    time.sleep(min(2 ** attempt, 30) * (0.5 + random.random() / 2))


def _extract_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from a model reply.

    Tolerates fenced code blocks and leading prose, but never silently returns
    an empty object: an unparseable reply raises.
    """
    text = (text or "").strip()
    if not text:
        raise ProviderParseError("provider returned an empty body")
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, depth, in_str, esc = None, 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None
    raise ProviderParseError(f"no JSON object found in reply: {text[:200]!r}")


def _payload(family: str, pin: str, prompt: str) -> Dict[str, Any]:
    _st = bool(SAMPLING_COMPAT.get(family, {"send_temperature": True}).get("send_temperature", True))
    if family == "anthropic":
        p = {
            "model": pin,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if _st:
            p["temperature"] = TEMPERATURE
        return p
    # openai and mistral are both OpenAI-chat shaped, except the token-cap
    # parameter name: current OpenAI models reject "max_tokens" outright and
    # require "max_completion_tokens" (observed live 2026-08-20, pin
    # gpt-5.6-sol; dated deviation 2026-08-21b).
    token_key = "max_completion_tokens" if family == "openai" else "max_tokens"
    cap = int(SAMPLING_COMPAT.get(family, {}).get("max_output_tokens", MAX_OUTPUT_TOKENS))
    p = {
        "model": pin,
        token_key: cap,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    if _st:
        p["temperature"] = TEMPERATURE
    return p


def _reply_text(family: str, body: Dict[str, Any]) -> str:
    try:
        if family == "anthropic":
            parts = body.get("content") or []
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderParseError(
            f"unexpected {family} response envelope: {json.dumps(body)[:200]}"
        ) from exc


def call_provider(
    family: str,
    pin: str,
    prompt: str,
    *,
    api_key: Optional[str] = None,
    client: Optional["httpx.Client"] = None,
) -> Dict[str, Any]:
    """One request/response round trip. Returns the parsed JSON object.

    ``api_key`` is only used under ``auth_mode="env_key"``. Under the default
    proxy-injected mode it is ``None`` and no auth header is sent.
    """
    if family not in ENDPOINTS:
        raise ProviderError(f"no live endpoint configured for family {family!r}")
    cfg = ENDPOINTS[family]
    headers = dict(cfg["headers"])
    if api_key:
        if family == "anthropic":
            headers["x-api-key"] = api_key
        else:
            headers["authorization"] = f"Bearer {api_key}"

    owns = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    last: Optional[Exception] = None
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                r = http.post(cfg["url"], headers=headers, json=_payload(family, pin, prompt))
            except httpx.HTTPError as exc:
                last = ProviderError(f"{family} transport error: {exc}")
                if attempt == MAX_ATTEMPTS:
                    break
                _sleep_backoff(attempt)
                continue
            if r.status_code == 200:
                return _extract_json(_reply_text(family, r.json()))
            if r.status_code in (408, 409, 425, 429) or r.status_code >= 500:
                last = ProviderError(f"{family} HTTP {r.status_code}: {r.text[:200]}")
                if attempt == MAX_ATTEMPTS:
                    break
                _sleep_backoff(attempt)
                continue
            # 4xx that will not improve on retry — fail fast and loudly
            raise ProviderError(f"{family} HTTP {r.status_code}: {r.text[:300]}")
    finally:
        if owns:
            http.close()
    raise last or ProviderError(f"{family}: exhausted {MAX_ATTEMPTS} attempts")
