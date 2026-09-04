"""Report writers: CSV, JSON and a human-readable text summary."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .models import Lead, VERDICT_LABELS_HE

COLUMNS = [
    "name", "phone", "phone_e164", "tel_link", "address", "city",
    "verdict", "verdict_he", "score", "website", "rating", "review_count",
    "maps_url", "place_id", "evidence", "checked_at",
]


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def write_csv(leads: list[Lead], out_dir: Path, stamp: str | None = None) -> Path:
    """UTF-8 BOM so Hebrew opens correctly in Excel on double-click."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"leads_{stamp or _stamp()}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(lead.to_row())
    return path


def write_json(leads: list[Lead], out_dir: Path, stamp: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"leads_{stamp or _stamp()}.json"
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(leads),
        "leads": [lead.to_row() for lead in leads],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def format_summary(leads: list[Lead], max_items: int = 40) -> str:
    """Plain-text report - printed to the terminal and sent to Telegram/email."""
    if not leads:
        return "לא נמצאו לידים חדשים בריצה הזו."

    by_verdict: dict[str, int] = {}
    for lead in leads:
        key = VERDICT_LABELS_HE[lead.verdict]
        by_verdict[key] = by_verdict.get(key, 0) + 1

    lines = [f"נמצאו {len(leads)} מספרות ללא מערכת תורים:", ""]
    for label, count in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        lines.append(f"  • {label}: {count}")
    lines.append("")

    for i, lead in enumerate(leads[:max_items], 1):
        p = lead.place
        rating = f"⭐ {p.rating} ({p.review_count})" if p.rating else "ללא דירוג"
        lines.append(f"{i}. {p.name} — {p.phone_e164 or 'אין טלפון'}")
        lines.append(
            f"   {p.address} | {rating} | {VERDICT_LABELS_HE[lead.verdict]} "
            f"[{lead.score}]"
        )

    if len(leads) > max_items:
        lines.append("")
        lines.append(f"...ועוד {len(leads) - max_items}. הרשימה המלאה בקובץ ה-CSV.")
    return "\n".join(lines)
