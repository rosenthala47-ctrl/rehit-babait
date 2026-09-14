"""ממשק שורת פקודה למנוע דירוג הסיכון.

Examples
--------
    risk-rating --customer "יוסי" --amount 100000
    risk-rating -c C1003 -a 250000 --explain
    risk-rating --list
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .data import AmbiguousCustomer, CustomerNotFound
from .engine import RiskResult
from .explain import ai_available, explain
from .service import DEFAULT_CONFIG, DEFAULT_DATA_DIR, RiskRatingService

_DECISION_ICON = {"approve": "✅", "review": "⚠️", "reject": "⛔"}
_RED = "\033[91m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def format_adjudication(adj: dict) -> str:
    """Render the discretionary second-look decision (override shown in red)."""
    if not adj:
        return ""
    out: List[str] = []
    if adj.get("is_override"):
        out.append("")
        out.append(f"{_RED}{_BOLD}⚠ החלטת חריגה בשיקול דעת (Override) → "
                   f"{adj['final_label_he']} ⚠{_RESET}")
        out.append(f"{_RED}הלקוח אינו עומד במלוא הקריטריונים, אך התקבלה החלטה "
                   f"לאחר בחינת התמונה המלאה.{_RESET}")
        out.append(f"{_RED}נימוק: {adj['rationale_he']}{_RESET}")
        if adj.get("conditions_he"):
            out.append(f"{_RED}תנאים: {adj['conditions_he']}{_RESET}")
        out.append(f"{_DIM}(מקור: {adj['source']} · רמת ביטחון: {adj['confidence']}){_RESET}")
    elif adj.get("blocked_reasons"):
        out.append("")
        out.append(f"{_DIM}שיקול דעת (Override): {adj['rationale_he']}{_RESET}")
    else:
        out.append("")
        out.append(f"{_DIM}שיקול דעת: {adj['rationale_he']}{_RESET}")
    return "\n".join(out)


def _fmt_amount(amount: float, currency: str) -> str:
    symbol = {"ILS": "₪", "USD": "$", "EUR": "€"}.get(currency, "")
    return f"{amount:,.0f} {symbol}".strip()


def _fmt_value(cs) -> str:
    if cs.missing:
        return "— (חסר נתון)"
    val = cs.raw_value
    if isinstance(val, float):
        return str(int(val)) if val.is_integer() else f"{val:.2f}"
    return str(val)


def _bureau_line(report) -> str:
    if not report:
        return ""
    if report.get("status") == "ok":
        score = report.get("bureau_score")
        score_txt = f"דירוג לשכה {score:.0f}" if isinstance(score, (int, float)) else "נשלף"
        return (f"מרשם אשראי:  ✓ בנק ישראל · {score_txt} · "
                f"ת\"ז {report.get('national_id_masked', '')} · {report.get('retrieved_at', '')}")
    return f"מרשם אשראי:  ⚠ {report.get('message', 'לא נשלף')}"


def format_report(result: RiskResult) -> str:
    icon = _DECISION_ICON.get(result.decision_id, "•")
    width = 60
    line = "─" * width
    dline = "═" * width

    out: List[str] = []
    out.append(dline)
    out.append("  דירוג סיכון לקוח  ·  Customer Risk Rating")
    out.append(dline)
    out.append(f"לקוח:        {result.full_name}  ({result.customer_id})")
    out.append(f"סכום מבוקש:  {_fmt_amount(result.requested_amount, result.currency)}")
    bureau = _bureau_line(result.external_report)
    if bureau:
        out.append(bureau)
    out.append(line)
    out.append(f"ציון סיכון:  {result.score:.1f} / 10   (מעוגל: {result.score_rounded})")
    out.append(f"החלטה:       {icon}  {result.decision_label_he}")
    out.append(f"פעולה:       {result.decision_action_he}")
    out.append(line)
    out.append("פירוט לפי קריטריון (ממוין לפי תרומה לסיכון):")
    for cs in sorted(result.breakdown, key=lambda c: c.contribution, reverse=True):
        weight_pct = f"{cs.weight * 100:.0f}%"
        out.append(
            f"  • [{weight_pct:>3}]  תת-ציון {cs.risk:>4.0f}/10  ·  "
            f"תרומה {cs.contribution:>4.2f}  ·  {cs.label_he} = {_fmt_value(cs)}"
        )
    out.append(dline)
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="risk-rating",
        description="מנוע דירוג סיכון לקוח — Customer risk-rating engine.",
    )
    p.add_argument("-c", "--customer", help="שם הלקוח או מזהה (למשל 'יוסי' או C1001)")
    p.add_argument("-a", "--amount", type=float, default=None,
                   help="סכום ההלוואה המבוקש (ברירת מחדל: 100000)")
    p.add_argument("--data", default=str(DEFAULT_DATA_DIR),
                   help="תיקיית טבלאות הנתונים (CSV/Excel)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG),
                   help="קובץ מודל הניקוד (YAML)")
    p.add_argument("--list", action="store_true", help="הצגת כל הלקוחות במערכת")
    p.add_argument("--json", action="store_true", help="פלט JSON במקום דוח קריא")
    p.add_argument("--explain", action="store_true",
                   help="הוספת הסבר מילולי להחלטה")
    p.add_argument("--ai", action="store_true",
                   help="שימוש ב-Claude לחוות דעת חיתום (דורש ANTHROPIC_API_KEY)")
    p.add_argument("--no-consent", dest="consent", action="store_false", default=True,
                   help="הלקוח לא נתן הסכמה — לא תישלח בקשה למרשם האשראי של בנק ישראל")
    p.add_argument("--purpose", default=None,
                   help="מטרת ההלוואה (למשל debt_consolidation, vehicle, business, investment)")
    p.add_argument("--collateral", default=None,
                   help="בטוחה (full_secured / partial_secured / guarantor / unsecured)")
    p.add_argument("--adjudicate", action="store_true",
                   help="הפעלת שיקול דעת (שכבת AI/היוריסטיקה שיכולה לאשר חריגה)")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        service = RiskRatingService(data_dir=args.data, config_path=args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"שגיאה בטעינת המערכת: {exc}", file=sys.stderr)
        return 2

    if args.list:
        customers = service.list_customers()
        print(f"נמצאו {len(customers)} לקוחות (טבלאות: {', '.join(service.store.tables)}):")
        for cid, name in customers:
            print(f"  {cid}  ·  {name}")
        return 0

    if not args.customer:
        print("יש לציין לקוח עם --customer/-c (או להשתמש ב---list). "
              "לדוגמה: risk-rating -c יוסי -a 100000", file=sys.stderr)
        return 2

    amount = args.amount
    note = ""
    if amount is None:
        amount = 100000.0
        note = "  (לא צוין סכום — משתמש בברירת מחדל 100,000)"

    try:
        result = service.assess(args.customer, amount, consent=args.consent,
                                purpose=args.purpose, collateral=args.collateral,
                                adjudicate=args.adjudicate, use_ai=args.ai)
    except AmbiguousCustomer as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except CustomerNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.ai and not ai_available():
        print("(שים לב: --ai בוקש אך אין מפתח ANTHROPIC_API_KEY או חבילת anthropic; "
              "מוצג הסבר רגיל)\n", file=sys.stderr)

    if args.json:
        payload = result.to_dict()
        if args.explain or args.ai:
            payload["explanation"] = explain(result, use_ai=args.ai)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if note:
        print(note.strip())
    print(format_report(result))
    if result.adjudication:
        print(format_adjudication(result.adjudication))
    if args.explain or args.ai:
        print("\nהסבר:")
        print(explain(result, use_ai=args.ai))
    return 0


if __name__ == "__main__":
    sys.exit(main())
