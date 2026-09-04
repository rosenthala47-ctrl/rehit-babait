"""Command-line interface: sapar-radar run | export | mark | stats | platforms."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, require_env
from .doctor import run_doctor
from .export import format_summary, write_csv, write_json
from .models import VERDICT_LABELS_HE
from .pipeline import Pipeline
from .providers import GoogleCSEProvider, GooglePlacesProvider, MockProvider
from .providers.mock import MockWebSearch
from .store import CONTACT_STATUSES, Store
from .website_probe import WebsiteProbe

log = logging.getLogger("sapar_radar")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader - avoids a python-dotenv dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sapar-radar",
        description="מוצא מספרות ללא אתר או אפליקציית תורים, ומחזיר מספרי טלפון.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--config", type=Path, help="path to config.yaml")
    parser.add_argument(
        "--db", type=Path, default=Path("out/sapar_radar.db"), help="state database"
    )

    # The same global flags again, accepted *after* the subcommand too, because
    # `run --area X -v` is what people naturally type. SUPPRESS keeps an absent
    # flag from overwriting the value already parsed before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true",
        default=argparse.SUPPRESS, help="debug logging",
    )
    common.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="path to config.yaml"
    )
    common.add_argument(
        "--db", type=Path, default=argparse.SUPPRESS, help="state database"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", parents=[common],
        help="run a full discovery + classification pass",
    )
    run.add_argument("--mock", action="store_true",
                     help="use offline fixtures - no API key needed")
    run.add_argument("--area", action="append",
                     help="override config areas (repeatable)")
    run.add_argument("--query", action="append",
                     help="override config queries (repeatable)")
    run.add_argument("--limit", type=int, help="stop after N leads")
    run.add_argument("--min-score", type=int, help="override filters.min_score")
    run.add_argument("--no-probe", action="store_true",
                     help="skip fetching shop homepages")
    run.add_argument("--web-verify", action="store_true",
                     help="cross-check each shop against Google web search")
    run.add_argument("--notify", action="store_true",
                     help="send the report to Telegram/email")
    run.add_argument("--dry-run", action="store_true",
                     help="print results without writing files or state")

    export = sub.add_parser("export", parents=[common], help="re-export everything reported so far")
    export.add_argument("--limit", type=int, default=1000)

    mark = sub.add_parser("mark", parents=[common], help="set the contact status of a shop")
    mark.add_argument("identifier", help="phone number or place_id")
    mark.add_argument("status", choices=CONTACT_STATUSES)
    mark.add_argument("--notes")

    doctor = sub.add_parser(
        "doctor", parents=[common],
        help="check your setup and report exactly what is missing",
    )
    doctor.add_argument(
        "--offline", action="store_true",
        help="skip the live Google API test",
    )

    sub.add_parser(
        "stats", parents=[common], help="show pipeline counters from the database"
    )
    sub.add_parser(
        "platforms", parents=[common],
        help="list the booking platforms being detected",
    )
    return parser


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    if args.area:
        config.raw.setdefault("search", {})["areas"] = args.area
    if args.query:
        config.raw.setdefault("search", {})["queries"] = args.query
    if args.min_score is not None:
        config.raw.setdefault("filters", {})["min_score"] = args.min_score
    if args.no_probe:
        config.raw.setdefault("classification", {})["probe_websites"] = False
    if args.web_verify:
        config.raw.setdefault("classification", {})["web_verify"] = True

    web_search = None
    if args.mock:
        discovery = MockProvider()
        web_search = MockWebSearch()
        log.info("running in MOCK mode - no API calls, fixture data only")
    else:
        discovery = GooglePlacesProvider(
            api_key=require_env("GOOGLE_MAPS_API_KEY"),
            language=str(config.get("search.language", "he")),
            region=str(config.get("search.region", "il")),
        )
        if config.get("classification.web_verify", False):
            web_search = GoogleCSEProvider(
                api_key=require_env("GOOGLE_CSE_API_KEY"),
                cse_id=require_env("GOOGLE_CSE_ID"),
                language=str(config.get("search.language", "he")),
                region=str(config.get("search.region", "il")),
            )

    store = None if args.dry_run else Store(args.db)
    probe = WebsiteProbe(
        timeout=float(config.get("classification.probe_timeout_seconds", 10))
    )
    pipeline = Pipeline(config, discovery, web_search, store, probe)

    run_id = store.start_run(discovery.name) if store else None
    try:
        leads = pipeline.run(limit=args.limit)
    finally:
        probe.close()
        for closeable in (discovery, web_search):
            if hasattr(closeable, "close"):
                closeable.close()

    stats = pipeline.stats
    log.info(
        "discovered=%d unique=%d leads=%d skipped=%s",
        stats.discovered, stats.unique, stats.leads, stats.skipped or "{}",
    )

    summary = format_summary(
        leads, max_items=int(config.get("notify.max_leads_in_message", 40))
    )
    print()
    print(summary)

    if args.dry_run:
        print("\n[dry-run] לא נכתבו קבצים ולא עודכן מסד הנתונים.")
        return 0

    out_dir = Path(str(config.get("output.dir", "out")))
    written: list[Path] = []
    formats = config.get("output.formats", ["csv", "json"]) or []
    if leads:
        if "csv" in formats:
            written.append(write_csv(leads, out_dir))
        if "json" in formats:
            written.append(write_json(leads, out_dir))
        for path in written:
            print(f"\nנכתב: {path}")

    if store and run_id is not None:
        store.finish_run(run_id, stats.discovered, stats.leads)
        store.close()

    if args.notify and leads:
        _notify(config, summary, written)
    return 0


def _notify(config: Config, summary: str, attachments: list[Path]) -> None:
    from .notify import send_email, send_telegram

    if config.get("notify.telegram", False):
        print("טלגרם:", "נשלח" if send_telegram(summary) else "נכשל")
    if config.get("notify.email", False):
        csvs = [p for p in attachments if p.suffix == ".csv"]
        ok = send_email("לידים חדשים - מספרות ללא מערכת תורים", summary, csvs)
        print("אימייל:", "נשלח" if ok else "נכשל")


def cmd_export(args: argparse.Namespace, config: Config) -> int:
    store = Store(args.db)
    rows = store.reported_rows(limit=args.limit)
    if not rows:
        print("אין לידים במסד הנתונים. הרץ קודם: sapar-radar run")
        return 1

    import csv as csv_module
    from datetime import datetime

    out_dir = Path(str(config.get("output.dir", "out")))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"all_leads_{datetime.now():%Y-%m-%d_%H%M}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    store.close()
    print(f"יוצאו {len(rows)} לידים אל {path}")
    return 0


def cmd_mark(args: argparse.Namespace, _config: Config) -> int:
    store = Store(args.db)
    updated = store.mark(args.identifier, args.status, args.notes)
    store.close()
    if not updated:
        print(f"לא נמצאה רשומה עבור {args.identifier!r}")
        return 1
    print(f"עודכנו {updated} רשומות ל-{args.status!r}")
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    return run_doctor(config, skip_live=args.offline)


def cmd_stats(args: argparse.Namespace, _config: Config) -> int:
    store = Store(args.db)
    for key, value in sorted(store.stats().items()):
        print(f"{key:28} {value}")
    store.close()
    return 0


def cmd_platforms(_args: argparse.Namespace, config: Config) -> int:
    platforms = config.platforms
    print(f"פלטפורמות תורים שמזוהות ({len(platforms.booking)}):")
    for domain in sorted(platforms.booking):
        print(f"  {domain}")
    print(f"\nדומיינים של סושיאל ({len(platforms.social)}):")
    print("  " + ", ".join(sorted(platforms.social)))
    print(f"\nבוני אתרים ({len(platforms.builders)}):")
    print("  " + ", ".join(sorted(platforms.builders)))
    print(f"\nביטויי זימון תור ({len(platforms.keywords)}):")
    print("  " + ", ".join(platforms.keywords))
    print("\nסיווגים:")
    for verdict, label in VERDICT_LABELS_HE.items():
        print(f"  {verdict.value:22} {label}")
    return 0


COMMANDS = {
    "run": cmd_run,
    "doctor": cmd_doctor,
    "export": cmd_export,
    "mark": cmd_mark,
    "stats": cmd_stats,
    "platforms": cmd_platforms,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()
    try:
        config = Config.load(args.config)
        return COMMANDS[args.command](args, config)
    except ConfigError as exc:
        print(f"שגיאת הגדרות: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nהופסק על ידי המשתמש.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
