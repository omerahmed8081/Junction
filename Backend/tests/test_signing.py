"""The signing module on its own: what it signs, and what it refuses.

Everything the three tokens rest on is here, so the failures are the point.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.signing import (  # noqa: E402
    ACCEPTED_ALGS,
    ALG,
    MAX_TOKEN_AGE_SECONDS,
    KeyPair,
    Signer,
    SigningError,
    VerificationError,
    Verifier,
    generate_keypair,
    jwks_document,
    load_or_create_keypair,
    now_seconds,
    read_header,
    read_payload_unverified,
)

NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)
MINE = generate_keypair("mine-1")
YOURS = generate_keypair("yours-1")


def claims(**extra):
    base = {"iss": "me", "aud": "you", "jti": "t_1",
            "iat": now_seconds(NOW), "exp": now_seconds(NOW) + 3600}
    base.update(extra)
    return base


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_a_signed_token_verifies_and_round_trips():
    token = Signer(MINE).sign(claims(net_total=1010.0))
    payload = Verifier([MINE.public_jwk]).verify(token, issuer="me", audience="you", now=NOW)
    assert payload["net_total"] == 1010.0 and payload["jti"] == "t_1"


def test_the_header_names_the_modern_algorithm():
    """RFC 9864 renamed EdDSA to Ed25519; we sign with the new name."""
    token = Signer(MINE).sign(claims())
    assert read_header(token) == {"alg": "Ed25519", "kid": "mine-1"}
    assert ALG == "Ed25519" and "EdDSA" in ACCEPTED_ALGS


def test_dates_and_decimals_can_be_signed():
    from datetime import date
    from decimal import Decimal

    token = Signer(MINE).sign(claims(requested_date=date(2026, 11, 17), total=Decimal("1010.00")))
    payload = Verifier([MINE.public_jwk]).verify(token, now=NOW)
    assert payload["requested_date"] == "2026-11-17" and payload["total"] == 1010.0


def test_something_unserialisable_is_our_error_not_a_traceback():
    try:
        Signer(MINE).sign(claims(thing=object()))
    except SigningError:
        return
    raise AssertionError("an unserialisable payload should raise SigningError")


def test_a_changed_payload_does_not_verify():
    token = Signer(MINE).sign(claims(net_total=1010.0))
    head, body, signature = token.split(".")
    swapped = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    swapped["net_total"] = 1.0
    tampered = f"{head}.{b64(json.dumps(swapped).encode())}.{signature}"
    try:
        Verifier([MINE.public_jwk]).verify(tampered, now=NOW)
    except VerificationError as exc:
        assert "signature" in str(exc)
        # ...and the claimed figure is still readable for the audit entry.
        assert read_payload_unverified(tampered)["net_total"] == 1.0
        return
    raise AssertionError("a changed payload was accepted")


def test_another_partys_key_does_not_verify():
    token = Signer(YOURS).sign(claims())
    try:
        Verifier([MINE.public_jwk]).verify(token, now=NOW)
    except VerificationError as exc:
        assert "not a registered key" in str(exc)
        return
    raise AssertionError("a token from an unregistered key was accepted")


def test_the_same_kid_with_a_different_key_does_not_verify():
    """Naming a key we know is not the same as holding it."""
    impostor = generate_keypair("mine-1")
    token = Signer(impostor).sign(claims())
    try:
        Verifier([MINE.public_jwk]).verify(token, now=NOW)
    except VerificationError as exc:
        assert "signature" in str(exc)
        return
    raise AssertionError("a forged token with a known kid was accepted")


def test_an_unsigned_token_is_refused():
    forged = f"{b64(json.dumps({'alg': 'none', 'kid': 'mine-1'}).encode())}.{b64(json.dumps(claims()).encode())}."
    try:
        Verifier([MINE.public_jwk]).verify(forged, now=NOW)
    except VerificationError as exc:
        assert "not accepted" in str(exc)
        return
    raise AssertionError("alg none was accepted")


def test_an_hmac_token_is_refused():
    """The other classic: re-sign with HMAC using the public key as the secret."""
    forged = f"{b64(json.dumps({'alg': 'HS256', 'kid': 'mine-1'}).encode())}.{b64(json.dumps(claims()).encode())}.xxxx"
    try:
        Verifier([MINE.public_jwk]).verify(forged, now=NOW)
    except VerificationError as exc:
        assert "not accepted" in str(exc)
        return
    raise AssertionError("HS256 was accepted")


def test_expiry_is_required_and_enforced():
    verifier = Verifier([MINE.public_jwk])
    signer = Signer(MINE)
    for payload, message in (
        ({"iss": "me"}, "no exp"),
        (claims(exp=now_seconds(NOW) - 3600), "expired"),
    ):
        try:
            verifier.verify(signer.sign(payload), now=NOW)
        except VerificationError as exc:
            assert message in str(exc), exc
        else:
            raise AssertionError(f"{message} was accepted")


def test_a_minute_of_clock_skew_is_tolerated():
    token = Signer(MINE).sign(claims(exp=now_seconds(NOW) - 30))
    Verifier([MINE.public_jwk]).verify(token, now=NOW)        # 30 s past, inside the skew


def test_a_token_from_the_future_is_refused():
    token = Signer(MINE).sign(claims(iat=now_seconds(NOW) + 600))
    try:
        Verifier([MINE.public_jwk]).verify(token, now=NOW)
    except VerificationError as exc:
        assert "future" in str(exc)
        return
    raise AssertionError("a token issued in the future was accepted")


def test_a_very_old_token_is_refused_however_long_its_expiry():
    old = now_seconds(NOW) - MAX_TOKEN_AGE_SECONDS - 60
    token = Signer(MINE).sign(claims(iat=old, exp=now_seconds(NOW) + 3600))
    try:
        Verifier([MINE.public_jwk]).verify(token, now=NOW)
    except VerificationError as exc:
        assert "older than a day" in str(exc)
        return
    raise AssertionError("a token issued two days ago was accepted")


def test_the_wrong_issuer_or_audience_is_refused():
    token = Signer(MINE).sign(claims())
    verifier = Verifier([MINE.public_jwk])
    for kwargs, word in (({"issuer": "somebody"}, "issuer"), ({"audience": "nobody"}, "audience")):
        try:
            verifier.verify(token, now=NOW, **kwargs)
        except VerificationError as exc:
            assert word in str(exc)
        else:
            raise AssertionError(f"the wrong {word} was accepted")


def test_an_audience_list_is_honoured():
    token = Signer(MINE).sign(claims(aud=["you", "someone"]))
    Verifier([MINE.public_jwk]).verify(token, audience="someone", now=NOW)


def test_rubbish_is_refused_without_a_traceback():
    verifier = Verifier([MINE.public_jwk])
    for junk in ("", "not-a-token", "a.b", "a.b.c.d", "...", "!!!.!!!.!!!"):
        try:
            verifier.verify(junk, now=NOW)
        except VerificationError:
            continue
        raise AssertionError(f"{junk!r} was accepted")


def test_a_public_key_with_no_kid_cannot_be_registered():
    jwk = dict(MINE.public_jwk)
    jwk.pop("kid")
    try:
        Verifier([jwk])
    except VerificationError:
        return
    raise AssertionError("a key with no kid was registered")


def test_the_jwks_document_holds_only_public_material():
    document = jwks_document([MINE.public_jwk, YOURS.public_jwk])
    assert [k["kid"] for k in document["keys"]] == ["mine-1", "yours-1"]
    # "d" is the private scalar in an OKP key, and it must never be published.
    # Checked per key: the letter d also appears in "kid" and "Ed25519".
    assert all("d" not in key for key in document["keys"])
    assert all("d" in pair.private_jwk for pair in (MINE, YOURS))


def test_a_key_file_is_created_once_and_then_reused(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sub" / "key.json"
        first = load_or_create_keypair(path, "svc-1")
        again = load_or_create_keypair(path, "ignored-because-it-exists")
        assert first.kid == again.kid == "svc-1"
        assert first.private_jwk == again.private_jwk
        assert path.exists()


def test_verifier_lists_what_it_trusts():
    verifier = Verifier([MINE.public_jwk])
    verifier.add_jwks(jwks_document([YOURS.public_jwk]))
    assert verifier.known_kids == ["mine-1", "yours-1"]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")


# ----------------------------------------- what the public endpoint may serve


# The private scalar of an Ed25519 JWK is "d" (RFC 8037). The RSA ones are here
# too so this still holds if the algorithm ever changes.
PRIVATE_JWK_FIELDS = {"d", "p", "q", "dp", "dq", "qi", "k", "private_jwk"}


def test_neither_side_ever_serves_private_key_material():
    """A JWKS document is meant to be public - that is the point of publishing
    it - but only the public half. Serving "d" would let anyone forge that
    party's signatures, which would end the trust story the whole project
    rests on, so this is worth a test of its own.
    """
    from gateway import keys as gateway_keys
    from kitchen import keys as kitchen_keys

    for side, module in (("gateway", gateway_keys), ("kitchen", kitchen_keys)):
        document = module.own_jwks()
        assert document.get("keys"), f"{side} served no keys at all"
        for key in document["keys"]:
            leaked = sorted(set(key) & PRIVATE_JWK_FIELDS)
            assert not leaked, f"{side} serves private key material: {leaked}"
            assert key.get("kty") and key.get("x"), f"{side} served an unusable key"


def test_the_private_half_exists_and_stays_off_the_wire():
    """The pair really is a pair - otherwise the test above would pass on a
    keypair that had no private half at all, which proves nothing."""
    from gateway import keys as gateway_keys

    pair = gateway_keys.gateway_keypair()
    assert "d" in pair.private_jwk, "there is no private half to protect"
    assert "d" not in pair.public_jwk
