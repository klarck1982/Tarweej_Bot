"""نور يشترط telegram_username في كل طلب (وثائق Nour Ads v1.5) — لا يُرسل فارغاً أبداً. بلا قاعدة بيانات."""
import os

os.environ.setdefault("BOT_TOKEN", "1:a")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://x")

from app.bot import keyboards as K  # noqa: E402
from app.services import nour, orders as O  # noqa: E402


def _order(tg=None):
    return {"id": 7, "spec": {"daily": "5", "days": 3, "platform": "facebook", "goal": "post_promotion", "country": "SY",
                              "provinces": ["all"], "whatsapp": "+963933000000", "tg_username": tg}}


def test_customer_username_wins():
    assert O.build_nour_payload(_order("samer"), "admin_fb")["telegram_username"] == "samer"


def test_fallback_used_when_customer_has_none():
    assert O.build_nour_payload(_order(None), "@admin_fb ")["telegram_username"] == "admin_fb"
    assert O.build_nour_payload(_order(""), "admin_fb")["telegram_username"] == "admin_fb"


def test_empty_when_nothing_so_submit_blocks():
    assert O.build_nour_payload(_order(None), "")["telegram_username"] == ""


def test_customer_username_is_cleaned():
    assert O.build_nour_payload(_order("@samer"), "")["telegram_username"] == "samer"


def test_missing_field_errors_are_not_retried():
    for code in ("missing_field", "rest_missing_callback_param", "invalid_telegram_username", "validation_error"):
        assert not nour.NourError(code).retryable, code
    assert nour.NourError("network").retryable


def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_skip_button_only_with_fallback():
    assert "meta:uname_skip" in _cbs(K.meta_username_missing(True))
    assert "meta:uname_skip" not in _cbs(K.meta_username_missing(False))
    assert "meta:uname_check" in _cbs(K.meta_username_missing(False))
