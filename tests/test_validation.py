"""التحقق من المدخلات (v0.9.2) — لا يحتاج قاعدة بيانات."""
import copy
import os
from decimal import Decimal

import pytest

os.environ.setdefault("BOT_TOKEN", "1:a")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@h/d")

from app.services import payments as PM  # noqa: E402
from app.services import pricing as P  # noqa: E402
from app.services import validators as V  # noqa: E402

TRC = {"kind": "crypto", "network": "TRC20"}
BEP = {"kind": "crypto", "network": "BEP20"}
SHAM = {"kind": "shamcash"}


@pytest.mark.parametrize("raw,expected", [
    ("10", Decimal("10")), ("25.5", Decimal("25.5")), ("5.55", Decimal("5.55")), ("٥٠", Decimal("50")),
    ("۱۲٫۵", Decimal("12.5")), ("7,25", Decimal("7.25")), ("$12", Decimal("12")), (" 9 ", Decimal("9")),
])
def test_parse_usd_accepts(raw, expected):
    assert V.parse_usd(raw) == expected


@pytest.mark.parametrize("raw", ["5.555", "1e3", "NaN", "inf", "Infinity", "-5", "abc", "", "1.2.3", "10$$x", "٥.٥٥٥"])
def test_parse_usd_rejects(raw):
    assert V.parse_usd(raw) is None


def test_trc20_address():
    good = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert len(good) == 34 and PM.clean_address(TRC, good) == good
    assert PM.clean_address(TRC, "  " + good + " ") == good
    assert PM.clean_address(TRC, "0x" + "ab" * 20) is None               # عنوان BEP20 في TRC20
    assert PM.clean_address(TRC, good[:-1]) is None                      # حرف ناقص
    assert PM.clean_address(TRC, good[:-1] + "0") is None                # 0 ليس من Base58
    assert PM.clean_address(TRC, "hello-this-is-not-an-address") is None


def test_bep20_address():
    good = "0x" + "aB" * 20
    assert PM.clean_address(BEP, good) == good
    assert PM.clean_address(BEP, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") is None
    assert PM.clean_address(BEP, "0x" + "g" * 40) is None
    assert PM.clean_address(BEP, "0x" + "a" * 39) is None


def test_shamcash_unchanged():
    assert PM.clean_address(SHAM, "1234 5678") == "12345678"
    assert PM.clean_address(SHAM, "12") is None


def test_pricing_rejects_free_services():
    base = P.validate(copy.deepcopy(P.DEFAULTS))
    for mutate in (lambda b: b.__setitem__("voiceover", "0"),
                   lambda b: b["addons"]["copy"].__setitem__("price", "0"),
                   lambda b: b["addon_bundles"][0].__setitem__("price", "0"),
                   lambda b: b["addon_bundles"][0].__setitem__("was", "1")):
        body = copy.deepcopy(base)
        mutate(body)
        with pytest.raises(ValueError):
            P.validate(body)


def test_pricing_defaults_still_valid():
    P.validate(copy.deepcopy(P.DEFAULTS))
