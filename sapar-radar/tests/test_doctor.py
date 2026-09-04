"""The doctor is what a first-time user sees when something is wrong,
so its verdicts and its advice both have to be right."""

import httpx
import pytest

from sapar_radar.config import Config
from sapar_radar.doctor import (
    FAIL, OK, WARN, check_api_key, check_api_live, check_notify, check_python,
    run_doctor,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "GOOGLE_MAPS_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "SMTP_HOST", "EMAIL_TO",
    ):
        monkeypatch.delenv(var, raising=False)


def test_python_version_passes_on_supported_runtime():
    assert check_python().mark == OK


def test_missing_api_key_is_a_blocking_failure():
    check = check_api_key()
    assert check.mark == FAIL
    assert check.failed
    assert "--mock" in check.detail       # tells them what they CAN do


def test_short_api_key_warns_but_does_not_block(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "too-short")
    check = check_api_key()
    assert check.mark == WARN
    assert not check.failed


def test_valid_looking_key_is_masked_not_leaked(monkeypatch):
    secret = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", secret)
    check = check_api_key()
    assert check.mark == OK
    assert secret not in check.title      # never print the whole key


def _live_check_with(monkeypatch, status, payload):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "A" * 39)

    def fake_post(*_args, **_kwargs):
        return httpx.Response(
            status, json=payload, request=httpx.Request("POST", "https://x")
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    return check_api_live()


def test_live_check_reports_success(monkeypatch):
    check = _live_check_with(monkeypatch, 200, {"places": [{"id": "x"}]})
    assert check.mark == OK


def test_live_check_explains_a_disabled_api(monkeypatch):
    check = _live_check_with(
        monkeypatch, 403,
        {"error": {"message": "Places API has not been used in project 123"}},
    )
    assert check.failed
    assert "לא מופעל" in check.title
    assert "Enable" in check.fix


def test_live_check_explains_missing_billing(monkeypatch):
    check = _live_check_with(
        monkeypatch, 403, {"error": {"message": "BILLING_DISABLED for project"}}
    )
    assert check.failed
    assert "חיוב" in check.title


def test_live_check_explains_an_invalid_key(monkeypatch):
    check = _live_check_with(
        monkeypatch, 400, {"error": {"message": "API key not valid."}}
    )
    assert check.failed
    assert "לא תקין" in check.title


def test_live_check_shows_the_sentence_not_raw_json(monkeypatch):
    check = _live_check_with(
        monkeypatch, 400, {"error": {"message": "API key not valid."}}
    )
    assert check.detail == "API key not valid."
    assert "{" not in check.detail


def test_live_check_survives_a_network_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "A" * 39)

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)
    check = check_api_live()
    assert check.failed
    assert "אינטרנט" in check.fix


def test_notify_warns_when_configured_but_unset():
    config = Config.load()
    config.raw["notify"] = {"telegram": True, "email": False}
    checks = check_notify(config)
    assert any(c.mark == WARN and "טלגרם" in c.title for c in checks)


def test_notify_warns_when_no_channel_at_all():
    config = Config.load()
    config.raw["notify"] = {"telegram": False, "email": False}
    checks = check_notify(config)
    assert any("אין ערוץ שליחה" in c.title for c in checks)


def test_doctor_exit_code_signals_blocking_problems(capsys):
    config = Config.load()
    assert run_doctor(config, skip_live=True) == 1   # no API key in clean env
    assert "--mock" in capsys.readouterr().out


def test_doctor_passes_when_everything_is_set(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "A" * 39)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    config = Config.load()
    config.raw["notify"] = {"telegram": True}
    assert run_doctor(config, skip_live=True) == 0
    assert "הכל תקין" in capsys.readouterr().out
