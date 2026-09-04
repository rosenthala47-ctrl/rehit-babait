"""Argument parsing. The flag-position tests exist because `run --area X -v`
is what people type, and argparse rejects it unless the subparsers opt in."""

from pathlib import Path

import pytest

from sapar_radar.cli import build_parser


def parse(argv):
    return build_parser().parse_args(argv)


def test_verbose_after_the_subcommand():
    assert parse(["run", "--mock", "-v"]).verbose is True


def test_verbose_before_the_subcommand():
    assert parse(["-v", "run", "--mock"]).verbose is True


def test_verbose_absent_stays_false():
    """SUPPRESS must not leave the attribute missing or flip the default."""
    assert parse(["run", "--mock"]).verbose is False


def test_verbose_before_is_not_clobbered_by_the_subparser():
    """The regression this design guards: a subparser default overwriting a
    value already parsed from before the subcommand."""
    assert parse(["-v", "run"]).verbose is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--db", "/tmp/a.db", "run"],
        ["run", "--db", "/tmp/a.db"],
    ],
)
def test_db_flag_in_either_position(argv):
    assert parse(argv).db == Path("/tmp/a.db")


def test_db_has_a_default():
    assert parse(["run"]).db == Path("out/sapar_radar.db")


@pytest.mark.parametrize(
    "command", ["run", "export", "mark", "doctor", "stats", "platforms"]
)
def test_every_subcommand_accepts_verbose(command):
    argv = [command, "-v"]
    if command == "mark":
        argv = ["mark", "+972501112233", "contacted", "-v"]
    assert parse(argv).verbose is True


def test_mark_rejects_an_unknown_status():
    with pytest.raises(SystemExit):
        parse(["mark", "+972501112233", "definitely_not_a_status"])


def test_run_flags_round_trip():
    args = parse(
        ["run", "--area", "חיפה", "--area", "נתניה", "--query", "מספרה",
         "--limit", "5", "--min-score", "85", "--no-probe", "--dry-run"]
    )
    assert args.area == ["חיפה", "נתניה"]
    assert args.query == ["מספרה"]
    assert args.limit == 5
    assert args.min_score == 85
    assert args.no_probe and args.dry_run
