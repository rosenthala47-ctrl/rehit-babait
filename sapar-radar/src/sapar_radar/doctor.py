"""`sapar-radar doctor` - tells a first-time user exactly what is missing.

Every failure here is something a beginner will otherwise hit as a stack trace
or an empty result. Each check reports what is wrong *and* the fix.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import CONFIG_DIR, Config

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


class Check:
    def __init__(self, mark: str, title: str, detail: str = "", fix: str = ""):
        self.mark, self.title, self.detail, self.fix = mark, title, detail, fix

    @property
    def failed(self) -> bool:
        return self.mark == FAIL

    def render(self) -> str:
        out = f"{self.mark} {self.title}"
        if self.detail:
            out += f"\n     {self.detail}"
        if self.fix:
            out += f"\n     ← {self.fix}"
        return out


def check_python() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        return Check(
            FAIL,
            f"פייתון {major}.{minor} - ישן מדי",
            "הסוכן דורש פייתון 3.10 ומעלה.",
            "התקן גרסה עדכנית מ-python.org והתקן מחדש: pip install -e .",
        )
    return Check(OK, f"פייתון {major}.{minor}")


def check_config() -> Check:
    if (CONFIG_DIR / "config.yaml").exists():
        return Check(OK, "קובץ הגדרות config/config.yaml קיים")
    return Check(
        WARN,
        "אין config/config.yaml - משתמש בברירת המחדל",
        "הסוכן יעבוד, אבל תסרוק את 15 הערים שבדוגמה ולא את שלך.",
        "cp config/config.example.yaml config/config.yaml",
    )


def check_api_key() -> Check:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        return Check(
            FAIL,
            "אין מפתח GOOGLE_MAPS_API_KEY",
            "אפשר להריץ בלי מפתח: sapar-radar run --mock (נתוני דמו) "
            "או sapar-radar run --osm (חיפוש אמיתי ב-OpenStreetMap, בלי כרטיס אשראי).",
            "צור מפתח ב-console.cloud.google.com ושים אותו בקובץ .env",
        )
    if len(key) < 30:
        return Check(
            WARN,
            "המפתח נראה קצר מדי",
            f"אורך {len(key)} תווים; מפתח אמיתי הוא בערך 39.",
            "ודא שהעתקת את המפתח במלואו, בלי רווחים.",
        )
    return Check(OK, f"מפתח Google קיים ({key[:6]}…{key[-4:]})")


def check_api_live(timeout: float = 20.0) -> Check:
    """One real Places call - the only way to know the key actually works."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        return Check(WARN, "בדיקת חיבור לגוגל - דולגה (אין מפתח)")

    import httpx

    from .providers.google_places import ENDPOINT

    try:
        response = httpx.post(
            ENDPOINT,
            json={"textQuery": "מספרה תל אביב", "maxResultCount": 1},
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.id,places.displayName",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return Check(
            FAIL,
            "לא הצלחתי להתחבר ל-Google Places",
            f"{type(exc).__name__}: {exc}",
            "בדוק חיבור אינטרנט / חומת אש / פרוקסי.",
        )

    if response.status_code == 200:
        count = len(response.json().get("places") or [])
        return Check(OK, f"חיבור ל-Google Places עובד (החזיר {count} תוצאה)")

    body = _error_message(response)
    if response.status_code == 400 and "api key not valid" in body.lower():
        return Check(
            FAIL,
            "המפתח לא תקין",
            body,
            "העתק אותו שוב מ-Google Cloud → APIs & Services → Credentials. "
            "שים לב לרווחים או תווים חסרים בסוף.",
        )
    if response.status_code in (401, 403):
        if "SERVICE_DISABLED" in body or "has not been used" in body:
            return Check(
                FAIL,
                "Places API לא מופעל בפרויקט",
                body,
                "ב-Google Cloud: APIs & Services → Enable APIs → "
                "חפש 'Places API (New)' → Enable. חכה דקה ונסה שוב.",
            )
        if "BILLING" in body.upper():
            return Check(
                FAIL,
                "לא מוגדר חשבון חיוב בפרויקט",
                body,
                "Google דורשת כרטיס אשראי גם בשביל הקרדיט החינמי. "
                "Google Cloud → Billing → Link a billing account.",
            )
        return Check(
            FAIL,
            f"גוגל דחתה את המפתח (HTTP {response.status_code})",
            body,
            "בדוק שהמפתח נכון ושההגבלות שלו מאפשרות Places API.",
        )
    if response.status_code == 429:
        return Check(
            FAIL,
            "חרגת ממכסת הבקשות",
            body,
            "חכה קצת ונסה שוב, או העלה את המכסה ב-Google Cloud → Quotas.",
        )
    return Check(FAIL, f"שגיאה מגוגל (HTTP {response.status_code})", body)


def _error_message(response) -> str:
    """Google returns a JSON error envelope; show the sentence, not the JSON."""
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = ""
    return (message or response.text)[:250].strip()


def check_notify(config: Config) -> list[Check]:
    checks: list[Check] = []

    wants_tg = bool(config.get("notify.telegram", False))
    has_tg = bool(
        os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
    )
    if wants_tg and not has_tg:
        checks.append(
            Check(
                WARN,
                "טלגרם מופעל בהגדרות אבל חסרים פרטים",
                "notify.telegram: true, אבל אין TELEGRAM_BOT_TOKEN/CHAT_ID.",
                "קח token מ-@BotFather ו-chat id מ-@userinfobot, שים ב-.env",
            )
        )
    elif has_tg:
        checks.append(Check(OK, "טלגרם מוגדר" + ("" if wants_tg else " (כבוי בקונפיג)")))

    wants_mail = bool(config.get("notify.email", False))
    has_mail = bool(os.environ.get("SMTP_HOST") and os.environ.get("EMAIL_TO"))
    if wants_mail and not has_mail:
        checks.append(
            Check(
                WARN,
                "אימייל מופעל בהגדרות אבל חסרים פרטים",
                "notify.email: true, אבל אין SMTP_HOST/EMAIL_TO.",
                "מלא את פרטי ה-SMTP ב-.env (ב-Gmail צריך App Password).",
            )
        )
    elif has_mail:
        checks.append(Check(OK, "אימייל מוגדר" + ("" if wants_mail else " (כבוי בקונפיג)")))

    if not checks:
        checks.append(
            Check(
                WARN,
                "אין ערוץ שליחה מוגדר",
                "התוצאות ייכתבו רק לקובץ CSV בתיקיית out/.",
                "רוצה שיישלח אליך? הגדר טלגרם או אימייל ב-.env",
            )
        )
    return checks


def check_env_file() -> Check:
    if Path(".env").exists():
        return Check(OK, "קובץ .env קיים")
    return Check(
        WARN,
        "אין קובץ .env",
        "המפתחות נקראים ממשתני סביבה. בלי .env תצטרך להגדיר אותם ידנית.",
        "cp .env.example .env  ואז ערוך אותו",
    )


def run_doctor(config: Config, skip_live: bool = False) -> int:
    print("בדיקת מערכת - sapar-radar\n" + "─" * 46)

    checks = [check_python(), check_env_file(), check_config(), check_api_key()]
    if not skip_live:
        checks.append(check_api_live())
    checks.extend(check_notify(config))

    for check in checks:
        print(check.render())

    failures = [c for c in checks if c.failed]
    print("─" * 46)
    if failures:
        word = "בעיה אחת שחוסמת" if len(failures) == 1 else f"{len(failures)} בעיות שחוסמות"
        print(f"\n{word} ריצה אמיתית.")
        print("בינתיים אפשר תמיד להריץ:  sapar-radar run --mock  (נתוני דמו)")
        print("או חיפוש אמיתי בלי מפתח:  sapar-radar run --osm   (OpenStreetMap)")
        return 1
    print("\nהכל תקין. נסה:  sapar-radar run --area \"תל אביב\" --limit 10")
    return 0
