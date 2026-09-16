"""التحقق من أن config يرفض الإعدادات الناقصة بوضوح بدل أن ينهار لاحقاً."""
import importlib
import os
import sys


def _reload_with(env: dict):
    for k in ["BOT_TOKEN", "DATABASE_URL", "ADMIN_IDS", "MODE", "PUBLIC_URL", "WEBHOOK_SECRET"]:
        os.environ.pop(k, None)
    os.environ.update(env)
    sys.modules.pop("app.config", None)
    return importlib.import_module("app.config")


def test_missing_token_exits(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    try:
        _reload_with({"DATABASE_URL": "postgresql://x", "ADMIN_IDS": "1"})
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("expected SystemExit")


def test_webhook_requires_public_url(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    try:
        _reload_with({"BOT_TOKEN": "1:a", "DATABASE_URL": "postgresql://x", "ADMIN_IDS": "1", "MODE": "webhook",
                      "WEBHOOK_SECRET": "abcdefghijklmnopqrstu"})
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("expected SystemExit")


def test_valid_polling(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    cfg = _reload_with({"BOT_TOKEN": "1:a", "DATABASE_URL": "postgresql://x", "ADMIN_IDS": "1, 2"})
    assert cfg.settings.admin_ids == (1, 2)
    assert cfg.settings.mode == "polling"
