"""Signed verification-state receipts for every gate decision.

Per ``docs/pre-registration.md``: *every* gate decision — pass, fail, replan —
emits a signed verification-state receipt, canonical bytes per RFC 8785 (JSON
Canonicalization Scheme), signed as an Ed25519 JWS, published alongside the raw
JSONL so a reader can verify offline that the published decisions are the ones
actually issued.

Receipts are append-only JSONL. Each line contains the receipt payload and its
compact JWS (``EdDSA``, header ``{"alg":"EdDSA","typ":"JOSE","kid":...}``),
plus the SHA-256 of the canonical payload bytes for cheap indexing.

Key material:
  * ``AGENTORACLE_RECEIPT_SK`` — 32-byte Ed25519 seed, base64url or hex.
  * otherwise a run-local development key is created under the receipt
    directory (``dev_signing_key.json``) and reused. Development keys are
    marked ``"dev_key": true`` in the receipt so they can never be mistaken
    for the published run's key.

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

RECEIPT_VERSION = "verification-state-receipt/1"

# Decision classes that must each produce a receipt.
DECISION_PASS = "pass"
DECISION_FAIL = "fail"
DECISION_REPLAN = "replan"
DECISION_CLASSES = (DECISION_PASS, DECISION_FAIL, DECISION_REPLAN)


# --------------------------------------------------------------------- RFC 8785
def _jcs_number(n: Any) -> str:
    """Serialize a number per RFC 8785 (ECMAScript ``Number::toString``)."""
    if isinstance(n, bool):  # bool is a subclass of int; handled by caller
        raise TypeError("bool is not a number")
    if isinstance(n, int):
        return str(n)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError("NaN/Infinity are not permitted in JCS")
    if n == 0:
        return "0"
    r = repr(float(n))
    if r.endswith(".0"):
        r = r[:-2]
    return r


def _jcs_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o == 0x08:
            out.append("\\b")
        elif o == 0x09:
            out.append("\\t")
        elif o == 0x0A:
            out.append("\\n")
        elif o == 0x0C:
            out.append("\\f")
        elif o == 0x0D:
            out.append("\\r")
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonicalize(value: Any) -> bytes:
    """Return RFC 8785 (JCS) canonical UTF-8 bytes for ``value``.

    Object members are sorted by UTF-16 code-unit order of their names, as the
    RFC requires; Python's default string ordering is by code point, which
    differs only for astral-plane keys, so keys are sorted on their UTF-16
    encoding explicitly.
    """
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, str):
        return _jcs_string(value).encode("utf-8")
    if isinstance(value, (int, float)):
        return _jcs_number(value).encode("utf-8")
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(canonicalize(v) for v in value) + b"]"
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=lambda k: str(k).encode("utf-16-be"))
        parts = []
        for k in keys:
            if not isinstance(k, str):
                raise TypeError("JCS object keys must be strings")
            parts.append(_jcs_string(k).encode("utf-8") + b":" + canonicalize(value[k]))
        return b"{" + b",".join(parts) + b"}"
    raise TypeError(f"not JSON-serializable for JCS: {type(value)!r}")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ------------------------------------------------------------------------ keys
def _load_seed(raw: str) -> bytes:
    raw = raw.strip()
    try:
        if len(raw) == 64:
            return bytes.fromhex(raw)
    except ValueError:
        pass
    seed = b64u_decode(raw)
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    return seed


@dataclass
class SigningKey:
    private: Ed25519PrivateKey
    kid: str
    dev_key: bool

    @property
    def public_jwk(self) -> Dict[str, str]:
        raw = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {"kty": "OKP", "crv": "Ed25519", "x": b64u(raw), "kid": self.kid}


def load_or_create_key(receipt_dir: pathlib.Path) -> SigningKey:
    env = os.environ.get("AGENTORACLE_RECEIPT_SK", "")
    if env:
        priv = Ed25519PrivateKey.from_private_bytes(_load_seed(env))
        dev = False
    else:
        receipt_dir.mkdir(parents=True, exist_ok=True)
        path = receipt_dir / "dev_signing_key.json"
        if path.exists():
            seed = b64u_decode(json.loads(path.read_text())["seed_b64u"])
            priv = Ed25519PrivateKey.from_private_bytes(seed)
        else:
            priv = Ed25519PrivateKey.generate()
            seed = priv.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            path.write_text(json.dumps({"seed_b64u": b64u(seed), "dev_key": True}, indent=2) + "\n")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        dev = True
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    if dev:
        kid = b64u(hashlib.sha256(raw_pub).digest()[:16])
    else:
        # Operator-supplied seed (AGENTORACLE_RECEIPT_SK): benchmark identity.
        # The prefix makes these receipts distinguishable at a glance from both
        # dev-key receipts and production receipts.
        kid = "benchmark-a-" + hashlib.sha256(raw_pub).hexdigest()[:16]
    return SigningKey(private=priv, kid=kid, dev_key=dev)


# -------------------------------------------------------------------- writer
class ReceiptWriter:
    """Emits one signed receipt per gate decision, append-only.

    Thread-safe and safe against concurrent processes: writes are a single
    ``write()`` of one line to a file opened in append mode, and the public key
    is published next to the receipts as ``public_key.jwk.json``.
    """

    def __init__(
        self,
        receipt_dir: str | pathlib.Path = "data/receipts",
        run_id: str = "",
        dry_run: bool = False,
        key: Optional[SigningKey] = None,
    ) -> None:
        self.dir = pathlib.Path(receipt_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or "unlabelled-run"
        self.dry_run = bool(dry_run)
        self.key = key or load_or_create_key(self.dir)
        suffix = "dryrun" if dry_run else "run"
        self.path = self.dir / f"receipts-{self.run_id}-{suffix}.jsonl"
        self._lock = threading.Lock()
        self._count = 0
        self.public_jwk_path = self.dir / "public_key.jwk.json"
        self.public_jwk_path.write_text(
            json.dumps(self.key.public_jwk, indent=2, sort_keys=True) + "\n"
        )

    # -- payload ----------------------------------------------------------
    def build_payload(
        self,
        *,
        decision_class: str,
        question_id: str,
        domain: str,
        model: str,
        agent: str,
        citation_id: str,
        citation: Dict[str, Any],
        claim: str,
        gate_outcome: str,
        gate_passed: bool,
        gate_reason: str = "",
        attempt: int = 1,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if decision_class not in DECISION_CLASSES:
            raise ValueError(f"decision_class must be one of {DECISION_CLASSES}")
        payload: Dict[str, Any] = {
            "version": RECEIPT_VERSION,
            "run_id": self.run_id,
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "experiment": "A",
            "agent": agent,
            "model": model,
            "domain": domain,
            "question_id": question_id,
            "citation_id": citation_id,
            "attempt": attempt,
            "decision_class": decision_class,
            "gate": {
                "endpoint": str(citation.get("_gate_endpoint", "")) or "",
                "outcome": gate_outcome,
                "passed": bool(gate_passed),
                "reason": gate_reason[:1000],
            },
            "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            "citation": {
                "url": citation.get("url", ""),
                "doi": citation.get("doi", ""),
                "title": citation.get("title", ""),
                "locator": citation.get("locator", ""),
            },
            "dry_run": self.dry_run,
            "dev_key": self.key.dev_key,
        }
        if extra:
            payload["extra"] = extra
        return payload

    # -- signing ----------------------------------------------------------
    def sign(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return ``{payload, protected, signature, jws, payload_sha256}``."""
        canon = canonicalize(payload)
        header = {"alg": "EdDSA", "kid": self.key.kid, "typ": "JOSE"}
        protected = b64u(canonicalize(header))
        body = b64u(canon)
        signing_input = f"{protected}.{body}".encode("ascii")
        sig = b64u(self.key.private.sign(signing_input))
        return {
            "payload": payload,
            "payload_sha256": hashlib.sha256(canon).hexdigest(),
            "protected": protected,
            "signature": sig,
            "jws": f"{protected}.{body}.{sig}",
        }

    def write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = self.sign(payload)
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._count += 1
        return record

    def emit(self, **kwargs: Any) -> Dict[str, Any]:
        """Build + sign + append in one call."""
        return self.write(self.build_payload(**kwargs))

    @property
    def count(self) -> int:
        return self._count


# ------------------------------------------------------------------ verifying
def verify_jws(jws: str, public_jwk: Dict[str, str]) -> Dict[str, Any]:
    """Verify a compact receipt JWS offline. Returns the decoded payload.

    Raises ``ValueError`` if the signature does not verify or the payload is not
    already in RFC 8785 canonical form (a receipt whose bytes are not canonical
    is not a receipt this harness issued).
    """
    protected_b64, body_b64, sig_b64 = jws.split(".")
    raw_pub = b64u_decode(public_jwk["x"])
    pub = Ed25519PublicKey.from_public_bytes(raw_pub)
    pub.verify(b64u_decode(sig_b64), f"{protected_b64}.{body_b64}".encode("ascii"))
    body = b64u_decode(body_b64)
    payload = json.loads(body.decode("utf-8"))
    if canonicalize(payload) != body:
        raise ValueError("receipt payload is not RFC 8785 canonical")
    return payload


def verify_file(path: str | pathlib.Path, public_jwk: Dict[str, str]) -> Dict[str, int]:
    """Verify every receipt in a JSONL file. Returns counts by decision class."""
    counts: Dict[str, int] = {"total": 0}
    for line in pathlib.Path(path).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        payload = verify_jws(rec["jws"], public_jwk)
        counts["total"] += 1
        cls = payload.get("decision_class", "unknown")
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def iter_receipts(path: str | pathlib.Path) -> Iterable[Dict[str, Any]]:
    for line in pathlib.Path(path).read_text().splitlines():
        if line.strip():
            yield json.loads(line)


__all__ = [
    "ReceiptWriter",
    "SigningKey",
    "canonicalize",
    "load_or_create_key",
    "verify_jws",
    "verify_file",
    "iter_receipts",
    "DECISION_PASS",
    "DECISION_FAIL",
    "DECISION_REPLAN",
    "RECEIPT_VERSION",
]
