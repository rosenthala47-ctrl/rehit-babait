"""שכבת ההסבר — הופכת את הציון להסבר קריא בעברית.

Two modes:

1. ``template_explanation`` — always available, no dependencies, no network.
   Deterministic summary of the decision and the main risk drivers.

2. ``ai_explanation`` — optional. If the ``anthropic`` package is installed
   and an API key is configured, Claude writes a short underwriter-style memo
   in Hebrew, *grounded in the numbers the engine already produced*. The
   deterministic score and decision remain authoritative — the model explains,
   it never re-decides. Any error (or a safety refusal) falls back to the
   template so the system never blocks on the AI.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .engine import RiskResult

# ברירת המחדל היא הדגם החזק ביותר. ניתן להוזיל דרך משתנה הסביבה, למשל:
#   export RISK_EXPLAIN_MODEL=claude-haiku-4-5
DEFAULT_MODEL = os.environ.get("RISK_EXPLAIN_MODEL", "claude-opus-5")


def _fmt_amount(amount: float, currency: str) -> str:
    symbol = {"ILS": "₪", "USD": "$", "EUR": "€"}.get(currency, "")
    return f"{amount:,.0f} {symbol}".strip()


def _fmt_raw(value: Any) -> str:
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    return str(value)


# ── deterministic explanation ────────────────────────────────────────────────
def template_explanation(result: RiskResult) -> str:
    """A dependency-free Hebrew explanation of the decision."""
    lines = []
    amount = _fmt_amount(result.requested_amount, result.currency)
    lines.append(
        f"הלקוח {result.full_name} ביקש הלוואה בסך {amount}. "
        f"ציון הסיכון שחושב הוא {result.score:.1f} מתוך 10 "
        f"(מעוגל ל-{result.score_rounded}) — ההמלצה: {result.decision_label_he}."
    )

    if result.knockouts:
        reasons = "; ".join(k["label_he"] for k in result.knockouts)
        lines.append(
            "⛔ נדחה עקב כלל נוק-אאוט — תנאי דחייה מוחלט שאינו תלוי בציון: "
            + reasons + ". (שכבת שיקול הדעת אינה רשאית לבטל דחייה זו.)")

    drivers = [c for c in result.top_risk_drivers(3) if c.risk >= 5]
    if drivers:
        parts = []
        for c in drivers:
            val = "חסר נתון" if c.missing else _fmt_raw(c.raw_value)
            parts.append(f"{c.label_he} (ערך: {val}, תת-ציון {c.risk:.0f}/10)")
        lines.append("גורמי הסיכון המרכזיים: " + "; ".join(parts) + ".")

    mitigants = sorted(
        [c for c in result.breakdown if c.risk <= 3 and not c.missing],
        key=lambda c: c.weight, reverse=True,
    )[:3]
    if mitigants:
        parts = [f"{c.label_he} (תת-ציון {c.risk:.0f}/10)" for c in mitigants]
        lines.append("גורמים ממתנים לטובת הלקוח: " + "; ".join(parts) + ".")

    missing = [c.label_he for c in result.breakdown if c.missing]
    if missing:
        lines.append(
            "שים לב — חסרים נתונים בקריטריונים הבאים, ולכן שוקללה הערכת סיכון "
            "ברירת-מחדל: " + ", ".join(missing) + "."
        )

    lines.append("פעולה מומלצת: " + result.decision_action_he)
    return "\n".join(lines)


# ── optional AI explanation ──────────────────────────────────────────────────
def ai_available() -> bool:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


_SYSTEM_PROMPT = (
    "אתה חתם אשראי בכיר בחברת אשראי. קיבלת פלט ממנוע דירוג-סיכון דטרמיניסטי "
    "ומבוקר: ציון סיכון בסקאלה 1–10 (נמוך = סיכון נמוך) והחלטה. "
    "תפקידך לכתוב חוות דעת חיתום קצרה, מקצועית ותמציתית בעברית. "
    "כללים: (1) הציון וההחלטה סופיים — אל תשנה אותם. "
    "(2) הסתמך אך ורק על הנתונים שסופקו; אל תמציא מידע. "
    "(3) ציין 2–3 גורמי הסיכון המרכזיים ואת הגורמים הממתנים. "
    "(4) אם חסרים נתונים — ציין זאת כהסתייגות. "
    "(5) סיים בהמלצת פעולה אחת ברורה. עד 180 מילים, ללא כותרות מיותרות."
)


def ai_explanation(result: RiskResult, model: Optional[str] = None,
                   max_tokens: int = 1200) -> str:
    """Ask Claude for a grounded underwriter memo; fall back to template on error."""
    if not ai_available():
        return template_explanation(result)

    try:
        import anthropic

        client = anthropic.Anthropic()
        payload: Dict[str, Any] = result.to_dict()
        user_content = (
            "להלן פלט מנוע הדירוג עבור הבקשה (JSON). כתוב את חוות הדעת בעברית.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        # thinking is omitted on purpose: on claude-opus-5 it runs adaptively,
        # and the call stays model-agnostic for cheaper models too.
        message = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        if getattr(message, "stop_reason", None) == "refusal":
            return template_explanation(result)

        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        ).strip()
        return text or template_explanation(result)
    except Exception as exc:  # noqa: BLE001 - never let the AI layer break scoring
        return (template_explanation(result)
                + f"\n\n(הערה: הסבר ה-AI לא היה זמין — {exc})")


def explain(result: RiskResult, use_ai: bool = False,
            model: Optional[str] = None) -> str:
    """Return a Hebrew explanation, optionally enriched by Claude."""
    if use_ai:
        return ai_explanation(result, model=model)
    return template_explanation(result)
