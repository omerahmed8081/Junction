"""Signing and verifying the three tokens that make this trustworthy.

Both services use this one module, so neither can be lax about what it accepts:

  * the **quote**    Aimo signs what it promises: price, delivery, origin, expiry
  * the **mandate**  the kitchen signs what its agent may order at all
  * the **approval** the kitchen signs one human's OK for one exact quote

Format: compact JWS (RFC 7515) over an Ed25519 key (RFC 8037), published as a
JWKS (RFC 7517). Any agent can verify one with a standard library and no code of
ours, which is the point of the openness claim.

`alg` is **"Ed25519"**, the identifier RFC 9864 introduced; the older "EdDSA"
name is still accepted when verifying so an older client interoperates.

Each service holds only its own private key. The public keys travel as JWKS:
Aimo's at its own /.well-known/jwks.json, the kitchen's registered with the
gateway at setup. A key is never fetched from a URL found inside a token, or
anyone could mint a mandate naming their own issuer.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from joserfc import jws
from joserfc.jwk import KeySet, OKPKey

ALG = "Ed25519"                       # RFC 9864's name for Ed25519 signing
ACCEPTED_ALGS = ("Ed25519", "EdDSA")  # verify either, sign only the first
CURVE = "Ed25519"

# A token older than this is refused however long its own exp was: a clock that
# is badly wrong should fail loudly rather than let a stale token through.
MAX_TOKEN_AGE_SECONDS = 24 * 60 * 60
CLOCK_SKEW_SECONDS = 60

_SIGN_REGISTRY = jws.JWSRegistry(algorithms=[ALG])
_VERIFY_REGISTRY = jws.JWSRegistry(algorithms=list(ACCEPTED_ALGS))


class SigningError(RuntimeError):
    """We could not sign: usually a missing or unreadable key."""


class VerificationError(RuntimeError):
    """The token is not acceptable. The message says why, for the audit log."""


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def now_seconds(now: datetime | None = None) -> int:
    return int((now or datetime.now(timezone.utc)).timestamp())


# --------------------------------------------------------------------- keys


@dataclass(frozen=True)
class KeyPair:
    """One service's signing key, plus the public half to hand out."""

    kid: str
    private_jwk: dict[str, Any]
    public_jwk: dict[str, Any]

    @property
    def key(self) -> OKPKey:
        return OKPKey.import_key(self.private_jwk)


def generate_keypair(kid: str | None = None) -> KeyPair:
    kid = kid or f"k-{secrets.token_hex(4)}"
    key = OKPKey.generate_key(CURVE, parameters={"kid": kid, "use": "sig", "alg": ALG})
    return KeyPair(kid=kid, private_jwk=key.as_dict(private=True),
                   public_jwk=key.as_dict(private=False))


def load_or_create_keypair(path: str | Path, kid: str) -> KeyPair:
    """Read the key from disk, creating it the first time.

    The file holds a private key, so it lives outside the repository tree we
    commit (`.keys/` is gitignored) and is written readable only by its owner
    where the platform supports that.
    """
    path = Path(path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return KeyPair(kid=data["kid"], private_jwk=data["private_jwk"],
                       public_jwk=data["public_jwk"])
    pair = generate_keypair(kid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kid": pair.kid, "private_jwk": pair.private_jwk,
                                "public_jwk": pair.public_jwk}, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass            # Windows, where this is not expressible the same way
    return pair


def jwks_document(public_jwks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """What /.well-known/jwks.json serves."""
    return {"keys": list(public_jwks)}


# ------------------------------------------------------------------ signing


class Signer:
    """Signs this service's tokens. Holds the one private key."""

    def __init__(self, keypair: KeyPair) -> None:
        self._keypair = keypair

    @property
    def kid(self) -> str:
        return self._keypair.kid

    @property
    def public_jwk(self) -> dict[str, Any]:
        return self._keypair.public_jwk

    def sign(self, payload: dict[str, Any]) -> str:
        """A compact JWS over the payload, as JSON.

        `iat` and `exp` are the caller's business: the three token builders set
        them, and verification insists on `exp`.
        """
        try:
            body = json.dumps(payload, default=_json_default, separators=(",", ":"),
                              sort_keys=True).encode("utf-8")
            return jws.serialize_compact({"alg": ALG, "kid": self._keypair.kid}, body,
                                         self._keypair.key, registry=_SIGN_REGISTRY)
        except TypeError as exc:
            raise SigningError(f"payload is not serialisable: {exc}") from exc
        except Exception as exc:        # noqa: BLE001 - surfaced as our own error
            raise SigningError(str(exc)) from exc


# --------------------------------------------------------------- verifying


class Verifier:
    """Verifies tokens against a set of public keys.

    Keys are added explicitly, never discovered from a token's own claims.
    """

    def __init__(self, public_jwks: Sequence[dict[str, Any]] | None = None) -> None:
        self._jwks: dict[str, dict[str, Any]] = {}
        for jwk in public_jwks or ():
            self.add(jwk)

    def add(self, jwk: dict[str, Any]) -> None:
        kid = jwk.get("kid")
        if not kid:
            raise VerificationError("a public key with no kid cannot be registered")
        self._jwks[kid] = jwk

    def add_jwks(self, document: dict[str, Any]) -> None:
        for jwk in document.get("keys") or ():
            self.add(jwk)

    @property
    def known_kids(self) -> list[str]:
        return sorted(self._jwks)

    def verify(
        self,
        token: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        now: datetime | None = None,
        require_expiry: bool = True,
    ) -> dict[str, Any]:
        """The payload, or VerificationError saying what was wrong.

        Checked in this order, so the reason in the audit log is the useful one:
        the header, then the signature, then expiry, then who it is for.
        """
        if not token or token.count(".") != 2:
            raise VerificationError("not a compact JWS")

        try:
            header = json.loads(_b64url_decode(token.split(".", 1)[0]))
        except Exception as exc:        # noqa: BLE001
            raise VerificationError("unreadable JWS header") from exc

        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ACCEPTED_ALGS:
            # "none" and the HMAC algorithms are the classic JWT forgeries.
            raise VerificationError(f"algorithm {alg!r} is not accepted")
        if not kid:
            raise VerificationError("the JWS header names no kid")
        if kid not in self._jwks:
            raise VerificationError(f"kid {kid!r} is not a registered key")

        try:
            key_set = KeySet([OKPKey.import_key(self._jwks[kid])])
            result = jws.deserialize_compact(token, key_set, registry=_VERIFY_REGISTRY)
        except Exception as exc:        # noqa: BLE001
            raise VerificationError("signature does not verify") from exc

        try:
            payload = json.loads(result.payload)
        except Exception as exc:        # noqa: BLE001
            raise VerificationError("payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise VerificationError("payload is not a JSON object")

        seconds = now_seconds(now)
        expiry = payload.get("exp")
        if expiry is None:
            if require_expiry:
                raise VerificationError("the token has no exp")
        elif not isinstance(expiry, (int, float)):
            raise VerificationError("exp is not a number")
        elif seconds > expiry + CLOCK_SKEW_SECONDS:
            raise VerificationError("the token has expired")

        issued = payload.get("iat")
        if isinstance(issued, (int, float)):
            if issued > seconds + CLOCK_SKEW_SECONDS:
                raise VerificationError("the token is issued in the future")
            if seconds - issued > MAX_TOKEN_AGE_SECONDS:
                raise VerificationError("the token is older than a day")

        if issuer is not None and payload.get("iss") != issuer:
            raise VerificationError(f"issuer is {payload.get('iss')!r}, expected {issuer!r}")

        if audience is not None:
            claimed = payload.get("aud")
            allowed = claimed if isinstance(claimed, list) else [claimed]
            if audience not in allowed:
                raise VerificationError(f"audience is {claimed!r}, expected {audience!r}")

        return payload


def _b64url_decode(segment: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def read_header(token: str) -> dict[str, Any]:
    """The unverified header, for logging which key a token claims to use."""
    try:
        return json.loads(_b64url_decode(token.split(".", 1)[0]))
    except Exception:       # noqa: BLE001
        return {}


def read_payload_unverified(token: str) -> dict[str, Any]:
    """The unverified payload.

    Only for an audit entry about a token that failed verification. Never make a
    decision on this.
    """
    try:
        parts = token.split(".")
        return json.loads(_b64url_decode(parts[1])) if len(parts) == 3 else {}
    except Exception:       # noqa: BLE001
        return {}
