from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


def load_cases(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(ln.startswith("{") and ln.endswith("}") for ln in lines):
        return [json.loads(ln) for ln in lines]

    # Fallback: JSON text sequence
    dec = json.JSONDecoder()
    i = 0
    cases: list[dict] = []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, j = dec.raw_decode(text, i)
        if isinstance(obj, dict):
            cases.append(obj)
        i = j
    return cases


def extract_enum_values(models_text: str, class_name: str) -> set[str]:
    start = models_text.find(f"class {class_name}")
    if start < 0:
        return set()
    # naive: stop at next class
    next_class = models_text.find("\n\nclass ", start + 1)
    block = models_text[start:next_class] if next_class > 0 else models_text[start:]
    pairs = re.findall(r'^\s+([A-Z0-9_]+)\s*=\s*"([A-Z0-9_]+)"\s*$', block, flags=re.M)
    return {v for _, v in pairs}


def extract_subtypes(parser_text: str) -> set[str]:
    return set(re.findall(r'return\s+"([a-z0-9_\.]+)"', parser_text))


def has_explicit_geo_cue(text: str) -> bool:
    t = text.lower()

    # Currency and region markers.
    if any(sym in text for sym in ["€", "£", "¥"]):
        return True
    if re.search(r"\b(us|u\.s\.|usa|american)\b", t):
        return True
    if re.search(r"\b(eu|europe|european|uk|british)\b", t):
        return True
    if re.search(r"\b(china|chinese|japan|tokyo|hong kong|singapore|india)\b", t):
        return True
    if re.search(r"\b(africa|nigeria|kenya|south africa)\b", t):
        return True
    if re.search(r"\b(australia|new zealand)\b", t):
        return True
    if re.search(r"\b(canada|brazil|mexico|argentina)\b", t):
        return True

    # Regulators and institutions often imply jurisdiction.
    if re.search(r"\b(sec|cftc|doj|finra|ofac|nyse|nasdaq)\b", t):
        return True
    if re.search(r"\b(fca|pra|bank of england|boe)\b", t):
        return True
    if re.search(r"\b(esma|ecb|european commission|brussels)\b", t):
        return True
    if re.search(r"\b(pbo c|pboc|people's bank of china|bank of japan)\b", t):
        return True

    # Political institutions.
    if "white house" in t or "congress" in t or "senate" in t:
        return True

    return False


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    synth_path = repo_root / "eval" / "synthetic_cases.jsonl"
    if not synth_path.exists():
        print("Missing eval/synthetic_cases.jsonl")
        return

    cases = load_cases(synth_path)

    models_text = (repo_root / "src" / "crypto_news_parser" / "models.py").read_text(
        encoding="utf-8"
    )
    parser_text = (repo_root / "src" / "crypto_news_parser" / "parser.py").read_text(
        encoding="utf-8"
    )

    event_types = extract_enum_values(models_text, "EventType")
    jurisdictions = extract_enum_values(models_text, "Jurisdiction")
    subtypes = extract_subtypes(parser_text)

    ids = [c.get("id") for c in cases]
    dup_ids = sorted({i for i in ids if i and ids.count(i) > 1})

    invalid: list[tuple[str | None, str, object]] = []
    missing_expected: list[str | None] = []

    for c in cases:
        cid = c.get("id")
        exp = c.get("expected")
        if not isinstance(exp, dict):
            missing_expected.append(cid)
            continue

        ev = exp.get("event_type")
        if ev not in event_types:
            invalid.append((cid, "event_type", ev))

        st = exp.get("event_subtype")
        if st is not None and st not in subtypes:
            invalid.append((cid, "event_subtype", st))

        j = exp.get("jurisdiction")
        if j is not None and j not in jurisdictions:
            invalid.append((cid, "jurisdiction", j))

    print(f"cases: {len(cases)}")
    if dup_ids:
        print(f"dup_ids ({len(dup_ids)}): {dup_ids}")
    if missing_expected:
        print(f"missing_expected ({len(missing_expected)}): {missing_expected}")

    if invalid:
        print(f"invalid ({len(invalid)}):")
        for row in invalid:
            print(" -", row)
    else:
        print("invalid: 0")

    expected_dicts = [c.get("expected") for c in cases if isinstance(c.get("expected"), dict)]

    ctr_event_type = Counter([e.get("event_type") for e in expected_dicts])
    print("\ncoverage_event_type:")
    for et in sorted(event_types):
        print(f"- {et}: {ctr_event_type.get(et, 0)}")

    ctr_jurisdiction = Counter(
        [e.get("jurisdiction") for e in expected_dicts if e.get("jurisdiction")]
    )
    print("\ncoverage_jurisdiction:")
    for j in sorted(jurisdictions):
        print(f"- {j}: {ctr_jurisdiction.get(j, 0)}")

    ctr_subtype = Counter(
        [e.get("event_subtype") for e in expected_dicts if e.get("event_subtype")]
    )
    print("\ncoverage_event_subtype (top 30):")
    for st, n in ctr_subtype.most_common(30):
        print(f"- {st}: {n}")

    ambiguous_geo: list[str] = []
    for c in cases:
        exp = c.get("expected")
        if not isinstance(exp, dict):
            continue
        if exp.get("jurisdiction") != "GLOBAL":
            continue
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if not has_explicit_geo_cue(text):
            cid = c.get("id")
            if isinstance(cid, str):
                ambiguous_geo.append(cid)

    print("\nambiguous_geo_candidates:")
    print(f"- count: {len(ambiguous_geo)}")
    if ambiguous_geo:
        print("- sample_ids:", ", ".join(ambiguous_geo[:25]))


if __name__ == "__main__":
    main()
