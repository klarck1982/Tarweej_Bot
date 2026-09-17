"""اختبارات التسعير (v0.3.0) — بدون شبكة أو قاعدة بيانات:  python -m pytest -q

القاعدة المتفق عليها: سعر العميل = الميزانية × 1.30 بالضبط (منزلتان، بلا تقريب لأعلى، بلا حد أدنى للرسوم)
تكلفة نور = الميزانية × 1.10 → الربح = 20% من الميزانية.
"""
from decimal import Decimal

import pytest

from app.services import pricing as P


def test_packages_final_table():
    by = P.META_BY_CODE
    assert (by["trial"].daily, by["trial"].days) == (Decimal("2"), 5)
    assert by["trial"].price == Decimal("13.00") and by["trial"].cost == Decimal("11.00")
    assert by["growth"].price == Decimal("27.30") and by["growth"].cost == Decimal("23.10")
    assert by["pro"].price == Decimal("65.00") and by["pro"].cost == Decimal("55.00")
    assert by["growth"].margin == Decimal("4.20")
    assert by["pro"].margin == Decimal("10.00")


def test_custom_exact_130():
    b, price, cost = P.meta_custom_price(3, 7)
    assert (b, price, cost) == (Decimal("21.00"), Decimal("27.30"), Decimal("23.10"))
    b, price, cost = P.meta_custom_price(10, 10)          # لا شرائح — دائماً ×1.30
    assert (price, cost) == (Decimal("130.00"), Decimal("110.00"))
    b, price, cost = P.meta_custom_price(10, 30)
    assert (price, cost) == (Decimal("390.00"), Decimal("330.00"))


def test_smallest_order_no_min_fee():
    b, price, cost = P.meta_custom_price(2, 1)            # أصغر طلب ممكن
    assert (b, price, cost) == (Decimal("2.00"), Decimal("2.60"), Decimal("2.20"))
    b, price, cost = P.meta_custom_price(Decimal("2.5"), 1)
    assert (price, cost) == (Decimal("3.25"), Decimal("2.75"))


def test_two_decimals_no_rounding_up():
    # 2.33 × 3 = 6.99 → ×1.3 = 9.087 → 9.09 (تقريب عادي لمنزلتين، لا لأعلى إلى دولار)
    b, price, cost = P.meta_custom_price(Decimal("2.33"), 3)
    assert b == Decimal("6.99") and price == Decimal("9.09") and cost == Decimal("7.69")
    assert price.as_tuple().exponent == -2


def test_custom_validation():
    with pytest.raises(ValueError):
        P.meta_custom_price(1, 5)          # أقل من 2$/يوم
    with pytest.raises(ValueError):
        P.meta_custom_price(5, 31)         # أكثر من 30 يوماً
    with pytest.raises(ValueError):
        P.meta_custom_price(5, 0)


def test_both_platforms_min_daily():
    assert P.META_BOTH_MIN_DAILY == Decimal("4")


def test_bundle_store_launch():
    b = P.BUNDLE_STORE_LAUNCH
    assert b["price"] == Decimal("38")
    # نمو 27.30 + نص 5 + صورة 8 = 40.30 → الحزمة أرخص
    assert P.bundle_separate_total() == Decimal("40.30")
    assert b["price"] < P.bundle_separate_total()


def test_addons_prices():
    assert P.ADDONS["copy"]["price"] == Decimal("5")
    assert P.ADDONS["design"]["price"] == Decimal("8")
    assert P.ADDONS["reel"]["price"] == Decimal("15")


def test_telegram_prices_unchanged():
    assert P.tg_ads_price(20) == Decimal("27.00")
    assert P.tg_post_price(8) == Decimal("10.00")
    assert P.tg_post_price(8, pinned=True) == Decimal("15.00")
    with pytest.raises(ValueError):
        P.tg_ads_price(5)


def test_fmt_and_days_word():
    assert P.fmt(13) == "13$"
    assert P.fmt("27.30") == "27.30$"
    assert P.fmt(Decimal("4.20")) == "4.20$"
    assert P.days_word(1) == "يوم واحد"
    assert P.days_word(2) == "يومان"
    assert P.days_word(7) == "7 أيام"
    assert P.days_word(14) == "14 يوماً"


def test_tg_ads_quote():
    b, price, cost = P.tg_ads_quote(25)
    assert (b, price, cost) == (Decimal("25.00"), Decimal("33.75"), Decimal("25.00"))
    b, price, cost = P.tg_ads_quote(10, copy_addon=True)
    assert price == Decimal("18.50") and cost == Decimal("10.00")
    with pytest.raises(ValueError):
        P.tg_ads_quote(9)
    with pytest.raises(ValueError):
        P.tg_ads_quote(501)
