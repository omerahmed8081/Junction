"""Check that every translation key and placeholder lines up.

    python scripts/check_i18n.py

TypeScript cannot see inside a translation string, so renaming a placeholder
from {{id}} to {{no}} compiles perfectly and puts "Order #{{no}} placed" on the
screen in front of a judge. That happened, so it gets a check.

Three things are checked:

  1. the languages agree on which keys exist
  2. a key uses the same placeholders in every language
  3. every call site supplies what its string asks for

It knows about i18next plurals (`key_one` / `key_other`, where `count` comes
from the option of that name) and about shorthand object properties, because
`t("policy.waiting", { who })` supplies `who` just as surely as `{ who: who }`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "Frontend"
SRC, I18N = ROOT / "src", ROOT / "src" / "i18n"

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
CALL = re.compile(r"""t\(\s*["']([\w.]+)["']\s*,\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}\s*\)""")
PLURAL = ("_one", "_other", "_zero", "_two", "_few", "_many")


def flatten(value: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, inner in value.items():
        full = f"{prefix}{key}"
        out.update(flatten(inner, full + ".") if isinstance(inner, dict) else {full: inner})
    return out


def supplied(args: str) -> set[str]:
    """The option names a call site passes, `{ who: x }` and `{ who }` alike."""
    names, depth, part = set(), 0, ""
    for char in args + ",":
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            piece = part.strip()
            if piece:
                names.add(piece.split(":")[0].strip().lstrip("."))
            part = ""
        else:
            part += char
    return {n for n in names if n.isidentifier()}


def wanted(base: dict[str, str], key: str) -> set[str] | None:
    """The placeholders a key needs, following plural forms. None if unknown."""
    if key in base:
        return set(PLACEHOLDER.findall(base[key]))
    forms = [base[key + s] for s in PLURAL if key + s in base]
    if not forms:
        return None
    # i18next fills `count` itself for a plural key.
    return set().union(*(set(PLACEHOLDER.findall(f)) for f in forms))


def main() -> int:
    if not I18N.is_dir():
        print(f"no translations at {I18N}")
        return 1
    langs = {p.stem: flatten(json.loads(p.read_text(encoding="utf-8")))
             for p in sorted(I18N.glob("*.json"))}
    base = langs.get("en")
    if base is None:
        print("no en.json to compare against")
        return 1

    problems: list[str] = []
    for lang, words in langs.items():
        if lang == "en":
            continue
        problems += [f"{lang}.json is missing {k}" for k in sorted(set(base) - set(words))]
        problems += [f"{lang}.json has {k}, which en.json does not"
                     for k in sorted(set(words) - set(base))]
        for key, text in base.items():
            if key in words and set(PLACEHOLDER.findall(words[key])) != set(PLACEHOLDER.findall(text)):
                problems.append(
                    f"{lang}.json {key} uses {sorted(set(PLACEHOLDER.findall(words[key])))}, "
                    f"en.json uses {sorted(set(PLACEHOLDER.findall(text)))}")

    for file in sorted(list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts"))):
        if file.name.endswith(".d.ts"):
            continue
        for key, args in CALL.findall(file.read_text(encoding="utf-8")):
            needs = wanted(base, key)
            where = file.relative_to(ROOT)
            if needs is None:
                problems.append(f'{where}: t("{key}") has no translation')
                continue
            has = supplied(args) | {"count"}          # i18next fills count itself
            for missing in sorted(needs - has):
                problems.append(f'{where}: t("{key}") needs {{{{{missing}}}}}, '
                                f'supplies {sorted(supplied(args)) or "nothing"}')

    print("\n".join(problems) if problems else
          f"{len(base)} keys in {len(langs)} languages: every key and placeholder lines up")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
