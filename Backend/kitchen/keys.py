"""The kitchen's signing key, and Aimo's public key.

The kitchen signs two things: the mandate, which says what its agent may order at
all, and an approval, which is one human's OK for one exact quote. It verifies
one thing: the quotes and confirmations Aimo signs.

Its own key is created on first run in `Backend/.keys/`, which is gitignored.
Aimo's public key is fetched from the gateway's JWKS once and cached, which is
safe because the gateway URL is configuration, not something read out of a token.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from shared.signing import KeyPair, Verifier, jwks_document, load_or_create_keypair

KEY_DIR = Path(os.environ.get("KEY_DIR") or Path(__file__).resolve().parent.parent / ".keys")
KITCHEN_URL = os.environ.get("KITCHEN_URL", "http://localhost:8002/restaurant-demo")
KITCHEN_KID = os.environ.get("KITCHEN_KID", "kitchen-2026-11")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8001")

def gateway_issuer() -> str:
    """The name the gateway signs as, and the name tokens are addressed to.

    Not the same thing as GATEWAY_URL, which is where to reach it. They were one
    variable, so quotes on the public site were issued by "http://localhost:8001",
    and the obvious fix - setting GATEWAY_URL to the public address - would have
    pointed the kitchen's own calls at the wrong place. GATEWAY_ISSUER is the
    public identity; unset, it is GATEWAY_URL, as it always was.
    """
    return os.environ.get("GATEWAY_ISSUER") or GATEWAY_URL


_aimo_verifier: Verifier | None = None


def kitchen_keypair() -> KeyPair:
    return load_or_create_keypair(KEY_DIR / "kitchen.json", KITCHEN_KID)


def own_jwks() -> dict:
    """What the kitchen serves so the gateway can register it."""
    return jwks_document([kitchen_keypair().public_jwk])


def aimo_verifier(timeout: float = 5.0) -> Verifier:
    """Aimo's public key, fetched from the gateway's JWKS and cached.

    If the gateway is not up yet the verifier is simply empty, and a quote then
    fails verification with "not a registered key", which is the honest answer.
    """
    global _aimo_verifier
    if _aimo_verifier is not None and _aimo_verifier.known_kids:
        return _aimo_verifier
    verifier = Verifier()
    local = os.environ.get("AIMO_JWKS_FILE")
    if local and Path(local).exists():
        import json

        verifier.add_jwks(json.loads(Path(local).read_text(encoding="utf-8")))
    else:
        try:
            response = httpx.get(f"{GATEWAY_URL}/.well-known/jwks.json", timeout=timeout)
            response.raise_for_status()
            verifier.add_jwks(response.json())
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] could not fetch Aimo's public key yet: {exc}")
    _aimo_verifier = verifier
    return verifier
