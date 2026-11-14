"""Is everything actually connected? Run this against the hosted services.

The 161 unit tests all run offline with stubs. This script is the opposite: it
touches every real moving part over HTTP in the order the demo does, and says
which one is broken. Run it after deploying, and again before the pitch.

    python scripts/smoke_test.py
    python scripts/smoke_test.py --kitchen https://kitchen.example --gateway https://gw.example

Exit code 0 means the demo path works end to end. Nothing here writes to the
catalogue; it does place a real order against the Aimo mock, so run the demo
reset afterwards if you care about the weekly spend (--reset does it for you).
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []
timings: dict[str, float] = {}


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    mark = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[status]
    print(f"{mark} {name}" + (f" — {detail}" if detail else ""), flush=True)


def step(client: httpx.Client, name: str, method: str, path: str,
         body: Any = None, expect: int = 200) -> Any:
    """One HTTP call, timed and recorded. Returns the parsed body or None."""
    start = time.perf_counter()
    try:
        response = client.request(method, path, json=body)
    except Exception as exc:                                   # noqa: BLE001
        record(name, FAIL, f"unreachable: {type(exc).__name__}: {exc}"[:160])
        return None
    timings[name] = time.perf_counter() - start
    if response.status_code != expect:
        record(name, FAIL, f"HTTP {response.status_code}: {response.text[:160]}")
        return None
    try:
        parsed = response.json()
    except Exception:                                          # noqa: BLE001
        record(name, FAIL, "response was not JSON")
        return None
    record(name, PASS, f"{timings[name] * 1000:.0f} ms")
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kitchen", default="http://localhost:8002")
    ap.add_argument("--gateway", default="http://localhost:8001")
    ap.add_argument("--message", default="1,000 L lactose-free milk by Tuesday")
    ap.add_argument("--role", default="owner", choices=["chef", "manager", "owner"])
    ap.add_argument("--gateway-key", default="demo-kitchen-key",
                    help="the agent key the gateway maps to a customer")
    ap.add_argument("--reset", action="store_true", help="reset both demo states first")
    ap.add_argument("--no-order", action="store_true", help="stop before placing the order")
    args = ap.parse_args()

    kitchen = httpx.Client(base_url=args.kitchen, timeout=60.0)
    gateway = httpx.Client(base_url=args.gateway, timeout=60.0,
                           headers={"X-API-Key": args.gateway_key})
    print(f"kitchen {args.kitchen}\ngateway {args.gateway}\n")

    # ---- 1. both services up, and the gateway's database reachable ---------
    gw_health = step(gateway, "gateway /health", "GET", "/health")
    if gw_health and gw_health.get("status") == "degraded":
        record("gateway database", FAIL,
               f"catalogue unreachable, search and quotes will fail: {gw_health.get('detail')}")
    elif gw_health:
        record("gateway database", PASS, f"{gw_health.get('products')} products loaded")
        if not gw_health.get("products"):
            record("catalogue loaded", FAIL, "0 products: run scripts/load_catalogue.py")
    step(kitchen, "kitchen /health", "GET", "/health")

    # ---- 2. keys published, so a quote can be verified by anyone ----------
    gw_jwks = step(gateway, "gateway JWKS", "GET", "/.well-known/jwks.json")
    if gw_jwks is not None:
        kids = [k.get("kid") for k in gw_jwks.get("keys", [])]
        if not kids:
            record("gateway public key", FAIL, "JWKS has no keys")
        elif any("d" in k for k in gw_jwks.get("keys", [])):
            record("gateway public key", FAIL, "PRIVATE KEY IS BEING SERVED — stop and fix")
        else:
            record("gateway public key", PASS, f"kid {kids[0]}")
    step(kitchen, "kitchen JWKS", "GET", "/.well-known/jwks.json")

    unknown = httpx.Client(base_url=args.gateway, timeout=20.0,
                           headers={"X-API-Key": "not-a-real-key"})
    try:
        turned_away = unknown.post("/gateway/search_products", json={"text": "milk"})
        record("unknown key is refused", PASS if turned_away.status_code == 401 else FAIL,
               f"HTTP {turned_away.status_code}" + ("" if turned_away.status_code == 401 else " — expected 401"))
    except Exception as exc:                                   # noqa: BLE001
        record("unknown key is refused", FAIL, f"{type(exc).__name__}: {exc}"[:120])

    if args.reset:
        step(kitchen, "kitchen demo reset", "POST", "/api/demo/reset")
        step(gateway, "gateway demo reset", "POST", "/gateway/demo/reset")

    # ---- 3. the dashboard's reads ----------------------------------------
    step(kitchen, "GET /api/inventory", "GET", "/api/inventory")
    step(kitchen, "GET /api/suggestions", "GET", "/api/suggestions")
    step(kitchen, "GET /api/mandate", "GET", "/api/mandate")
    step(kitchen, "GET /api/orders", "GET", "/api/orders")

    # ---- 4. the spine: sentence -> basket -> quote -> approval -> order ---
    spine_start = time.perf_counter()
    chat = step(kitchen, "POST /api/chat", "POST", "/api/chat",
                {"message": args.message, "basket_id": None})
    if not chat:
        return summarise()

    basket = chat.get("basket") or {}
    basket_id = basket.get("basket_id")
    lines = basket.get("lines") or []
    if not basket_id:
        record("basket created", FAIL, f"no basket_id in the reply: {str(chat)[:200]}")
        return summarise()
    if not lines:
        record("basket has lines", FAIL,
               "the basket came back empty — product not found, or every candidate is past its cut-off")
        return summarise()
    record("basket created", PASS,
           f"{len(lines)} line(s), {basket.get('net_total')} EUR net, parser={chat.get('parser') or '?'}")
    if chat.get("parser") == "rules":
        record("Gemini parser", WARN,
               "the rules parser answered: GEMINI_API_KEY or GEMINI_MODEL is wrong (check the kitchen log)")
    elif chat.get("parser"):
        record("Gemini parser", PASS, chat["parser"])

    step(kitchen, "GET /api/basket/{id}", "GET", f"/api/basket/{basket_id}")

    confirmed = step(kitchen, "POST confirm (signed quote)", "POST",
                     f"/api/basket/{basket_id}/confirm")
    if not confirmed:
        return summarise()
    cb = confirmed.get("basket") or {}
    if not cb.get("quote_jws"):
        record("quote is signed", FAIL, "confirm returned no quote_jws")
        return summarise()
    record("quote is signed", PASS, f"expires {cb.get('quote_expires')}")
    if cb.get("status") != "quoted":
        record("basket is quoted", WARN, f"status is {cb.get('status')!r}, expected 'quoted'")

    if args.no_order:
        record("order", WARN, "skipped (--no-order)")
        return summarise()

    ordered = step(kitchen, "POST approve (order placed)", "POST",
                   f"/api/basket/{basket_id}/approve", {"approver_role": args.role})
    if not ordered:
        return summarise()
    outcome = ordered.get("outcome")
    if outcome == "ordered":
        order = ordered.get("order") or {}
        record("order accepted by the gateway", PASS,
               f"{order.get('order_id')}, {order.get('net_total')} EUR net")
        timings["sentence to placed order"] = time.perf_counter() - spine_start
    elif outcome == "adjusted":
        record("order accepted by the gateway", WARN,
               f"basket adjusted instead: {(ordered.get('refusal') or {}).get('reason')} "
               "(this is correct behaviour, but the happy path did not complete)")
    else:
        record("order accepted by the gateway", FAIL,
               f"outcome={outcome} reason={(ordered.get('refusal') or {}).get('reason')}")

    # ---- 5. the guard: the same quote cannot be ordered twice -------------
    again = step(kitchen, "second approve is refused", "POST",
                 f"/api/basket/{basket_id}/approve", {"approver_role": args.role}, expect=409)
    if again is not None:
        record("single-use quote enforced", PASS, "")

    return summarise()


def summarise() -> int:
    failed = [r for r in results if r[1] == FAIL]
    warned = [r for r in results if r[1] == WARN]
    print("\n" + "-" * 60)
    if "sentence to placed order" in timings:
        print(f"sentence to placed order: {timings['sentence to placed order']:.1f} s")
    print(f"{len(results) - len(failed) - len(warned)} ok, {len(warned)} warning(s), {len(failed)} failure(s)")
    for name, _, detail in failed:
        print(f"  FAIL {name}: {detail}")
    for name, _, detail in warned:
        print(f"  warn {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
