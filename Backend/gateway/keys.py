"""The gateway's signing key, and the kitchen public keys it will accept.

Aimo's own key is created on first start and kept in `Backend/.keys/`, which is
gitignored: it is a private key and does not belong in the repository.

A kitchen's public key is **registered**, never discovered. The gateway reads a
list of trusted kitchen JWKS URLs from `KITCHEN_JWKS` (comma separated) and
fetches each once at startup; `KITCHEN_JWKS_FILE` points at a local JWKS for
development and tests. A key is never fetched from a URL inside a token, because
anyone could then mint a mandate naming their own issuer and have us trust it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from shared.signing import KeyPair, Verifier, jwks_document, load_or_create_keypair

KEY_DIR = Path(os.environ.get("KEY_DIR") or Path(__file__).resolve().parent.parent / ".keys")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8001")
GATEWAY_KID = os.environ.get("GATEWAY_KID", "aimo-2026-11")


def gateway_keypair() -> KeyPair:
    return load_or_create_keypair(KEY_DIR / "gateway.json", GATEWAY_KID)


def own_jwks() -> dict:
    return jwks_document([gateway_keypair().public_jwk])


def own_verifier() -> Verifier:
    """To check our own quotes and confirmations when they come back to us."""
    return Verifier([gateway_keypair().public_jwk])


def kitchen_verifier(timeout: float = 5.0) -> Verifier:
    """The kitchens we will accept mandates and approvals from."""
    verifier = Verifier()
    local = os.environ.get("KITCHEN_JWKS_FILE")
    if local and Path(local).exists():
        verifier.add_jwks(json.loads(Path(local).read_text(encoding="utf-8")))
    for url in (u.strip() for u in (os.environ.get("KITCHEN_JWKS") or "").split(",")):
        if not url:
            continue
        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            verifier.add_jwks(response.json())
        except Exception as exc:        # noqa: BLE001
            # Startup must not die because one kitchen is not up yet; an order
            # from it is refused with mandate_invalid, which says exactly this.
            print(f"[gateway] could not register kitchen keys from {url}: {exc}")
    return verifier
