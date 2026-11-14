"""Does the model call actually work? Run this when the chat falls back to the rules.

    python scripts/check_gemini.py

Prints what Gemini made of a few real requests, side by side with the rules
parser. On failure it prints Google's own error and then lists the models the
key can actually use, because a stale GEMINI_MODEL is the usual cause and the
fallback hides it.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                                  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from kitchen.need import parse_with_gemini, parse_with_rules    # noqa: E402

MESSAGES = [
    "1,000 L lactose-free milk by Tuesday",
    "I need 20 kg of butter for Thursday",
    "we're low on cream, get 50 litres for next Monday",
    "two cases of the 10 litre milk boxes for Monday please",
]


def list_models() -> None:
    """What this key may call, so a wrong GEMINI_MODEL is obvious."""
    try:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        print("\nModels this key can use (generateContent only):")
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or []
            if not actions or "generateContent" in actions:
                print(f"    {model.name}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"\nCould not list models either: {type(exc).__name__}: {exc}")


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL")
    print(f"GEMINI_API_KEY: {'set, ' + str(len(key)) + ' chars' if key else 'NOT SET'}")
    print(f"GEMINI_MODEL:   {model or 'NOT SET'}\n")
    if not key:
        print("No key, so the rules parser is the only one that can run.")
        return 1

    today = date(2026, 11, 14)
    failures = 0
    for message in MESSAGES:
        print(f">>> {message}")
        try:
            rules = parse_with_rules(message, today)
            print(f"    rules : {rules.product_text!r} {rules.quantity} {rules.unit} "
                  f"{rules.requested_date} claims={rules.claims}")
        except Exception as exc:                                # noqa: BLE001
            print(f"    rules : could not parse ({exc})")
        try:
            got = parse_with_gemini(message, today)
            print(f"    gemini: {got.product_text!r} {got.quantity} {got.unit} "
                  f"{got.requested_date} claims={got.claims} pack={got.pack_size}")
        except Exception as exc:                                # noqa: BLE001
            failures += 1
            print(f"    gemini: FAILED {type(exc).__name__}: {exc}")
        print()

    if failures:
        print(f"{failures} of {len(MESSAGES)} model calls failed.")
        list_models()
        print("\nPut a working name in GEMINI_MODEL in Backend/.env and restart the kitchen.")
        return 1
    print("All model calls worked. The chat uses Gemini, with the rules as the fallback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
