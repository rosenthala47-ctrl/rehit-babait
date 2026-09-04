"""End-to-end pipeline behaviour against the offline fixtures."""

import pytest

from sapar_radar.config import Config
from sapar_radar.models import Verdict
from sapar_radar.pipeline import Pipeline
from sapar_radar.providers.mock import MockProvider, MockWebSearch
from sapar_radar.store import Store


@pytest.fixture
def config():
    cfg = Config.load()
    cfg.raw.setdefault("search", {})["areas"] = ["תל אביב"]
    cfg.raw["search"]["queries"] = ["מספרה"]
    cfg.raw.setdefault("classification", {})["probe_websites"] = False
    return cfg


def run(config, store=None):
    pipeline = Pipeline(config, MockProvider(), MockWebSearch(), store, probe=None)
    return pipeline.run(), pipeline.stats


def test_finds_only_shops_without_booking(config):
    leads, stats = run(config)
    verdicts = {lead.verdict for lead in leads}
    assert Verdict.HAS_BOOKING not in verdicts
    assert stats.skipped["has_booking"] == 1     # the Booksy shop
    assert stats.skipped["closed"] == 1          # permanently closed
    assert stats.skipped["no_phone"] == 1        # useless as a lead


def test_every_lead_has_a_dialable_number(config):
    leads, _ = run(config)
    assert leads
    assert all(lead.place.phone_e164.startswith("+972") for lead in leads)


def test_leads_are_sorted_best_first(config):
    leads, _ = run(config)
    scores = [lead.score for lead in leads]
    assert scores == sorted(scores, reverse=True)
    assert leads[0].verdict is Verdict.NO_WEBSITE


def test_min_score_filter(config):
    config.raw["filters"]["min_score"] = 90
    leads, _ = run(config)
    assert all(lead.score >= 90 for lead in leads)
    assert len(leads) == 1


def test_limit_stops_early(config):
    pipeline = Pipeline(config, MockProvider(), MockWebSearch(), None, None)
    assert len(pipeline.run(limit=2)) == 2


def test_second_run_reports_nothing_new(config, tmp_path):
    store = Store(tmp_path / "state.db")
    first, _ = run(config, store)
    assert first
    second, stats = run(config, store)
    assert second == []
    assert stats.skipped["already_reported"] == len(first)
    store.close()


def test_do_not_contact_list_is_honoured(config):
    config.do_not_contact = {"+97235551234"}   # מספרת דוד
    leads, stats = run(config)
    assert all(lead.place.phone_e164 != "+97235551234" for lead in leads)
    assert stats.skipped["do_not_contact"] == 1


def test_min_review_count_filter(config):
    config.raw["filters"]["min_review_count"] = 100
    leads, _ = run(config)
    assert all(lead.place.review_count >= 100 for lead in leads)
