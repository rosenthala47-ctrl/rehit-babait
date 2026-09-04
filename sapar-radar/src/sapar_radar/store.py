"""SQLite state: de-duplication across runs and contact bookkeeping.

Without this the agent re-reports the same shops every night. With it, a run
only surfaces shops you have not already been given.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Lead, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    place_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    phone_e164    TEXT,
    address       TEXT,
    city          TEXT,
    website       TEXT,
    verdict       TEXT,
    score         INTEGER,
    evidence      TEXT,
    rating        REAL,
    review_count  INTEGER,
    maps_url      TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    reported_at   TEXT,
    contact_status TEXT NOT NULL DEFAULT 'new',
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_places_phone ON places(phone_e164);
CREATE INDEX IF NOT EXISTS idx_places_status ON places(contact_status);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    discovered  INTEGER DEFAULT 0,
    new_leads   INTEGER DEFAULT 0,
    provider    TEXT
);
"""

#: Statuses you can set with `sapar-radar mark`.
CONTACT_STATUSES = ("new", "reported", "contacted", "interested", "not_interested", "customer")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Thin SQLite wrapper. Safe to open per-run."""

    def __init__(self, path: str | Path = "out/sapar_radar.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- runs -------------------------------------------------------------
    def start_run(self, provider: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, provider) VALUES (?, ?)", (_now(), provider)
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, discovered: int, new_leads: int) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, discovered=?, new_leads=? WHERE id=?",
            (_now(), discovered, new_leads, run_id),
        )
        self.conn.commit()

    # -- places -----------------------------------------------------------
    def seen_before(self, place_id: str, phone_e164: str | None) -> bool:
        """True if this shop was already reported in an earlier run.

        Matches on place_id *or* phone: the same shop often appears under
        several Google listings (old name, second branch entry, duplicates).
        """
        row = self.conn.execute(
            "SELECT 1 FROM places WHERE place_id=? AND reported_at IS NOT NULL",
            (place_id,),
        ).fetchone()
        if row:
            return True
        if phone_e164:
            row = self.conn.execute(
                "SELECT 1 FROM places WHERE phone_e164=? AND reported_at IS NOT NULL",
                (phone_e164,),
            ).fetchone()
            if row:
                return True
        return False

    def upsert(self, lead: Lead, reported: bool) -> None:
        p = lead.place
        now = _now()
        self.conn.execute(
            """
            INSERT INTO places (
                place_id, name, phone_e164, address, city, website, verdict,
                score, evidence, rating, review_count, maps_url,
                first_seen, last_seen, reported_at, contact_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(place_id) DO UPDATE SET
                name=excluded.name,
                phone_e164=excluded.phone_e164,
                website=excluded.website,
                verdict=excluded.verdict,
                score=excluded.score,
                evidence=excluded.evidence,
                rating=excluded.rating,
                review_count=excluded.review_count,
                last_seen=excluded.last_seen,
                reported_at=COALESCE(places.reported_at, excluded.reported_at),
                contact_status=CASE
                    WHEN places.contact_status='new' AND excluded.reported_at IS NOT NULL
                    THEN 'reported' ELSE places.contact_status END
            """,
            (
                p.place_id, p.name, p.phone_e164, p.address, p.source_area,
                p.website, lead.verdict.value, lead.score,
                " | ".join(lead.evidence), p.rating, p.review_count, p.maps_url,
                now, now, now if reported else None,
                "reported" if reported else "new",
            ),
        )
        self.conn.commit()

    def mark(self, identifier: str, status: str, notes: str | None = None) -> int:
        """Set contact status by place_id or phone. Returns rows updated."""
        if status not in CONTACT_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {CONTACT_STATUSES}"
            )
        cur = self.conn.execute(
            "UPDATE places SET contact_status=?, notes=COALESCE(?, notes) "
            "WHERE place_id=? OR phone_e164=?",
            (status, notes, identifier, identifier),
        )
        self.conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT contact_status, COUNT(*) c FROM places GROUP BY contact_status"
        ).fetchall()
        out = {r["contact_status"]: r["c"] for r in rows}
        out["total"] = sum(out.values())
        by_verdict = self.conn.execute(
            "SELECT verdict, COUNT(*) c FROM places "
            "WHERE reported_at IS NOT NULL GROUP BY verdict"
        ).fetchall()
        for r in by_verdict:
            out[f"verdict:{r['verdict']}"] = r["c"]
        return out

    def reported_rows(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM places WHERE reported_at IS NOT NULL "
            "ORDER BY score DESC, reported_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
