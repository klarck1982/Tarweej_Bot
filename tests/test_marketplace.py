"""اختبارات سريعة لدوال سوق القنوات النقية (بلا قاعدة بيانات)."""
from decimal import Decimal

import pytest

from app.services import marketplace as MP


@pytest.mark.parametrize("raw,ref", [
    ("@damascus_deals", "@damascus_deals"), ("damascus_deals", "@damascus_deals"),
    ("https://t.me/damascus_deals", "@damascus_deals"), ("t.me/Damascus_Deals/", "@Damascus_Deals"),
    ("https://t.me/+AbCdEf", None), ("t.me/joinchat/xyz", None), ("@ab", None), ("hello world", None), ("", None),
])
def test_channel_ref(raw, ref):
    assert MP.channel_ref(raw) == ref


@pytest.mark.parametrize("raw,val", [("10", Decimal("10")), ("١٠", Decimal("10")), ("7.5$", Decimal("7.5")), ("12,5", Decimal("12.5"))])
def test_parse_price_ok(raw, val):
    assert MP.parse_price(raw) == val


@pytest.mark.parametrize("raw", ["abc", "0", "0.1", "-5", "9999999"])
def test_parse_price_bad(raw):
    with pytest.raises(MP.MPError):
        MP.parse_price(raw)


def test_parse_price_optional():
    assert MP.parse_price("", required=False) is None


def test_clean_address():
    assert MP.clean_address("usdt_trc20", " T" + "A" * 33 + " ") == "T" + "A" * 33
    assert MP.clean_address("usdt_bep20", "0x" + "a" * 40) == "0x" + "a" * 40
    assert MP.clean_address("shamcash", "٠٩٣٣  123 456") == "0933 123 456"
    for m, bad in (("usdt_trc20", "0x" + "a" * 40), ("usdt_bep20", "T" + "A" * 33), ("shamcash", "12"), ("paypal", "x@y.z")):
        with pytest.raises(MP.MPError):
            MP.clean_address(m, bad)


def test_rating_label():
    assert MP.rating_label({"rating_n": 0, "rating_sum": 0}) == ""
    assert "4.5" in MP.rating_label({"rating_n": 2, "rating_sum": 9})


def test_is_mp_channel():
    assert MP.is_mp_channel({"owner_user_id": 5, "chat_id": -100, "mp_status": "approved"})
    assert not MP.is_mp_channel({"owner_user_id": 5, "chat_id": -100, "mp_status": "draft"})
    assert not MP.is_mp_channel({"owner_user_id": None, "mp_status": None})
    assert not MP.is_mp_channel(None)
