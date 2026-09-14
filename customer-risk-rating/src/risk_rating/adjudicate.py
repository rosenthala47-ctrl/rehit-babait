"""שכבת שיקול דעת (adjudication) — "המוח" שמעל המנוע הדטרמיניסטי.

The deterministic engine produces a score and a by-the-book decision. This
layer adds judgment: it looks at the whole picture and may grant a
**discretionary exception** — approving a borderline applicant who does not
strictly meet the criteria but is fundamentally sound and merely early in
their credit life (thin file: young, short tenure, short history).

Two modes, like ``explain``:

* **AI mode** (``use_ai=True`` + ``ANTHROPIC_API_KEY``): Claude acts as a
  senior underwriter and reasons over the full breakdown.
* **Heuristic mode** (default / offline): a transparent "compensating
  factors" rule set.

**Guardrails are enforced in code either way** — neither Claude nor the
heuristic may override a hard negative (active default, unemployment,
unaffordable DTI, very low bureau score, severe delinquency, or a score too
high to rescue). The override can only make the decision *more lenient*, and
by at most one band. Every exception is flagged as a discretionary override
(shown in red in the UIs), never presented as meeting the criteria.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .engine import RiskResult
from .explain import DEFAULT_MODEL, ai_available

# leniency order (harshest -> most lenient)
_ORDER = ["reject", "review", "approve"]

# criteria that reflect *genuine* credit risk (must be clean to grant an exception)
_GENUINE_IDS = {
    "payment_history", "defaults", "credit_utilization", "affordability",
    "banking_conduct", "existing_leverage", "bureau_score",
}
# criteria typical of an early-career / thin-file profile (a legitimate reason to miss)
_THIN_FILE_IDS = {
    "employment_tenure", "credit_history_length", "age", "recent_inquiries",
}

ADJUDICATE_MODEL = os.environ.get("RISK_ADJUDICATE_MODEL", DEFAULT_MODEL)


@dataclass
class Adjudication:
    reviewed: bool                 # a second look ran
    is_override: bool              # the decision was changed to a more lenient one
    model_decision: str            # the by-the-book decision id
    final_decision: str            # decision id after adjudication
    final_label_he: str
    early_career_exception: bool
    confidence: str                # low | medium | high
    rationale_he: str
    conditions_he: str
    blocked_reasons: List[str]     # hard negatives that forbid any override
    source: str                    # "ai" | "heuristic" | "guardrail"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewed": self.reviewed,
            "is_override": self.is_override,
            "model_decision": self.model_decision,
            "final_decision": self.final_decision,
            "final_label_he": self.final_label_he,
            "early_career_exception": self.early_career_exception,
            "confidence": self.confidence,
            "rationale_he": self.rationale_he,
            "conditions_he": self.conditions_he,
            "blocked_reasons": self.blocked_reasons,
            "source": self.source,
        }


def _risk_by_id(result: RiskResult) -> Dict[str, float]:
    return {c.id: c.risk for c in result.breakdown}


def _raw_by_id(result: RiskResult) -> Dict[str, Any]:
    return {c.id: (None if c.missing else c.raw_value) for c in result.breakdown}


def _hard_blocks(result: RiskResult, record: Dict[str, Any]) -> List[str]:
    """Reasons an override must never be granted. Empty list = eligible."""
    risk = _risk_by_id(result)
    blocks: List[str] = []

    if (record.get("defaults") or 0) >= 1:
        blocks.append("כשל אשראי / הליך הוצל\"פ פעיל")
    if str(record.get("employment_status", "")).lower() == "unemployed":
        blocks.append("הלקוח מובטל")
    if risk.get("affordability", 0) >= 9:
        blocks.append("יחס החזר להכנסה (DTI) בלתי-אפשרי")
    if risk.get("bureau_score", 0) >= 8:
        blocks.append("דירוג לשכה נמוך מאוד")
    if risk.get("payment_history", 0) >= 7:
        blocks.append("היסטוריית פיגורים חמורה")
    if risk.get("banking_conduct", 0) >= 8:
        blocks.append("חריגות בנקאיות חמורות")
    if result.score_rounded >= 8:
        blocks.append("ציון סיכון גבוה מדי מכדי להצדיק חריגה")
    return blocks


def _band_label(model, decision_id: str) -> str:
    for b in model.decision_bands:
        if b.id == decision_id:
            return b.label_he
    return decision_id


def _clamp(model_decision: str, recommended: str) -> str:
    """Only ever more lenient, and by at most one band."""
    mi = _ORDER.index(model_decision)
    ri = _ORDER.index(recommended) if recommended in _ORDER else mi
    ri = max(mi, min(ri, mi + 1))
    return _ORDER[ri]


# ── heuristic (offline) ──────────────────────────────────────────────────────
def _heuristic(result: RiskResult, record: Dict[str, Any]) -> Dict[str, Any]:
    risk = _risk_by_id(result)
    # genuine credit risk, ignoring criteria with missing data (a gap, not a negative)
    genuine = [c.risk for c in result.breakdown
               if c.id in _GENUINE_IDS and not c.missing]
    genuine_max = max(genuine) if genuine else 10

    age = record.get("age")
    oldest = record.get("oldest_account_years")
    tenure = record.get("tenure_months")
    emp = str(record.get("employment_status", "")).lower()
    thin_reasons = []
    if age is not None and age <= 30:
        thin_reasons.append("גיל צעיר")
    if oldest is not None and oldest <= 3:
        thin_reasons.append("היסטוריית אשראי קצרה")
    if tenure is not None and tenure <= 24:
        thin_reasons.append("ותק תעסוקתי קצר")
    if emp in ("student", "employed_temp"):
        thin_reasons.append("תעסוקה בתחילת דרך")

    clean = (genuine_max <= 5 and risk.get("payment_history", 10) <= 3
             and (record.get("defaults") or 0) == 0)

    # eligible band: review, or a marginal reject (exactly 7)
    review_case = result.decision_id == "review"
    marginal_reject = result.score_rounded == 7

    if thin_reasons and clean and review_case:
        return {
            "override": True, "recommended": "approve",
            "early_career": True, "confidence": "medium",
            "rationale": (
                "הלקוח אינו עומד במלוא הקריטריונים, אך הפרופיל האשראי הבסיסי נקי "
                "(ללא פיגורים/כשלים, יכולת החזר סבירה) והפער נובע מגורמי \"תחילת דרך\": "
                + ", ".join(thin_reasons) + ". לאחר בחינת התמונה המלאה — אושר בשיקול דעת."),
            "conditions": "מומלץ: מעקב תשלומים ב-6 החודשים הראשונים; אפשר לשקול סכום/מסגרת מדורגים.",
        }
    if thin_reasons and genuine_max <= 3 and marginal_reject:
        return {
            "override": True, "recommended": "review",
            "early_career": True, "confidence": "low",
            "rationale": (
                "מקרה גבולי: הליבה האשראית איתנה אך הציון גבוה מעט בשל " + ", ".join(thin_reasons)
                + ". הומלץ להעביר לבדיקה ידנית במקום דחייה אוטומטית."),
            "conditions": "בדיקת חתם אנושי; לשקול ערב או סכום מופחת.",
        }
    return {
        "override": False, "recommended": result.decision_id,
        "early_career": bool(thin_reasons), "confidence": "medium",
        "rationale": (
            "התמונה המלאה נבחנה — לא נמצאה הצדקה מספקת לחריגה מהקריטריונים "
            "(הליבה האשראית אינה נקייה דיה או שאין גורמי \"תחילת דרך\" מובהקים)."),
        "conditions": "",
    }


# ── AI (Claude) ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "אתה חתם אשראי בכיר עם סמכות לשיקול דעת. קיבלת פלט ממנוע דירוג-סיכון "
    "דטרמיניסטי (ציון 1–10, נמוך=טוב) והחלטה 'לפי הספר'. תפקידך לבחון את התמונה "
    "המלאה ולהחליט אם להעניק **חריגה בשיקול דעת** ללקוח שאינו עומד בדיוק "
    "בקריטריונים אך הוא סולידי ביסודו ורק בתחילת דרכו האשראית (תיק דק: צעיר, "
    "ותק קצר, היסטוריה קצרה). כללי ברזל: אסור להקל אם יש כשל/הוצל\"פ פעיל, "
    "מובטלוּת, DTI בלתי-אפשרי, דירוג לשכה נמוך מאוד, פיגורים חמורים, או ציון>=8. "
    "מותר להקל לכל היותר בדרגה אחת (דחייה→בדיקה, בדיקה→אישור). "
    "החזר אך ורק JSON תקין במבנה: {\"override\": bool, \"recommended_decision\": "
    "\"approve\"|\"review\"|\"reject\", \"early_career_exception\": bool, "
    "\"confidence\": \"low\"|\"medium\"|\"high\", \"rationale_he\": str, "
    "\"conditions_he\": str}. הנימוק בעברית, תמציתי, ומדגיש שזו חריגה מודעת."
)


def _ai(result: RiskResult, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        import anthropic

        client = anthropic.Anthropic()
        case = {
            "model_score": result.score,
            "model_decision": result.decision_id,
            "requested_amount": result.requested_amount,
            "breakdown": [c.to_dict() for c in result.breakdown],
        }
        msg = client.messages.create(
            model=ADJUDICATE_MODEL,
            max_tokens=900,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       "בחן את המקרה והחזר JSON בלבד:\n\n"
                       + json.dumps(case, ensure_ascii=False)}],
        )
        if getattr(msg, "stop_reason", None) == "refusal":
            return None
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", None) == "text").strip()
        # tolerate code fences / stray text around the JSON object
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(text[start:end + 1])
        return {
            "override": bool(data.get("override")),
            "recommended": str(data.get("recommended_decision", result.decision_id)),
            "early_career": bool(data.get("early_career_exception")),
            "confidence": str(data.get("confidence", "medium")),
            "rationale": str(data.get("rationale_he", "")).strip(),
            "conditions": str(data.get("conditions_he", "")).strip(),
        }
    except Exception:  # noqa: BLE001 - never let the AI layer break scoring
        return None


# ── public entry point ───────────────────────────────────────────────────────
def adjudicate(result: RiskResult, record: Dict[str, Any], model,
               use_ai: bool = False) -> Adjudication:
    """Run a discretionary second look over a scored result."""
    blocks = _hard_blocks(result, record)
    if blocks:
        return Adjudication(
            reviewed=True, is_override=False,
            model_decision=result.decision_id, final_decision=result.decision_id,
            final_label_he=_band_label(model, result.decision_id),
            early_career_exception=False, confidence="high",
            rationale_he="נבחנה אפשרות לחריגה — נחסמה על ידי מעקות הבטיחות: "
                         + "; ".join(blocks) + ".",
            conditions_he="", blocked_reasons=blocks, source="guardrail",
        )

    ai_verdict = _ai(result, record) if (use_ai and ai_available()) else None
    if ai_verdict is not None:
        verdict, source = ai_verdict, "ai"
    else:
        verdict, source = _heuristic(result, record), "heuristic"

    final = _clamp(result.decision_id, verdict["recommended"]) if verdict["override"] \
        else result.decision_id
    is_override = final != result.decision_id

    return Adjudication(
        reviewed=True, is_override=is_override,
        model_decision=result.decision_id, final_decision=final,
        final_label_he=_band_label(model, final),
        early_career_exception=bool(verdict.get("early_career")),
        confidence=str(verdict.get("confidence", "medium")),
        rationale_he=verdict.get("rationale", ""),
        conditions_he=verdict.get("conditions", "") if is_override else "",
        blocked_reasons=[], source=source,
    )
