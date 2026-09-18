"""הדגמה של השימוש ב-API של Python.

    python examples/demo.py

Runs the whole customer book through the engine and prints a summary table,
then shows a full breakdown + explanation for a single request (Yossi, 100k).
"""
import pathlib
import sys

# Allow running from a checkout without installing the package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from risk_rating import RiskRatingService  # noqa: E402
from risk_rating.cli import format_report  # noqa: E402
from risk_rating.explain import explain  # noqa: E402


def main() -> None:
    service = RiskRatingService()
    amount = 100_000

    print(f"דירוג כל הלקוחות עבור בקשת הלוואה של {amount:,} ₪:\n")
    print(f"  {'ציון':>5}  {'החלטה':<12}  לקוח")
    print("  " + "-" * 46)
    for cid, name in service.list_customers():
        r = service.assess(cid, amount)
        icon = {"approve": "✅", "review": "⚠️", "reject": "⛔"}[r.decision_id]
        print(f"  {r.score:>5.1f}  {icon} {r.decision_label_he:<10}  {name} ({cid})")

    print("\n" + "=" * 60)
    print("דוגמה מלאה — יוסי כהן (C1001) מבקש 100,000 ₪:\n")
    # NOTE: two customers are named "יוסי כהן"; resolve by id or ת"ז, not by name.
    result = service.assess("C1001", amount)
    print(format_report(result))
    print("\nהסבר:")
    print(explain(result))  # use_ai=True ישתמש ב-Claude אם מוגדר מפתח API


if __name__ == "__main__":
    main()
