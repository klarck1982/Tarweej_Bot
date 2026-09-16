"""اختبارات التسعير — تعمل بدون شبكة أو قاعدة بيانات:  python -m pytest -q"""
from decimal import Decimal

import pytest

from app.services import pricing as P


def test_packages_match_blueprint():
    by = {p.code: p for p in P.META_PACKAGES}
    assert by["trial"].price == Decimal("14") and by["trial"].cost == Decimal("11.00")
    assert by["growth"].price == Decimal("28") and by["growth"].cost == Decimal("23.10")
    assert by["pro"].price == Decimal("65") and by["pro"].cost == Decimal("55.00")
    assert by["growth"].margin == Decimal("4.90")


def test_custom_tiers():
    b, price, cost = P.meta_custom_price(3, 7)      # 21$ → ×1.30
    assert (b, price, cost) == (Decimal("21.00"), Decimal("27.30"), Decimal("23.10"))
    b, price, _ = P.meta_custom_price(10, 10)       # 100$ → ×1.25
    assert price == Decimal("125.00")
    b, price, _ = P.meta_custom_price(10, 30)       # 300$ → ×1.20
    assert price == Decimal("360.00")


def test_custom_min_fee():
    b, price, _ = P.meta_custom_price(2, 1)         # 2$ → ×1.30 = 2.60 → رسوم 0.60 < 3 → 5$
    assert price == Decimal("5.00")


def test_custom_validation():
    with pytest.raises(ValueError):
        P.meta_custom_price(1, 5)
    with pytest.raises(ValueError):
        P.meta_custom_price(5, 31)


def test_telegram_prices():
    assert P.tg_ads_price(20) == Decimal("27.00")
    assert P.tg_post_price(8) == Decimal("10.00")
    assert P.tg_post_price(8, pinned=True) == Decimal("15.00")
    with pytest.raises(ValueError):
        P.tg_ads_price(5)


def test_fmt():
    assert P.fmt(14) == "14$"
    assert P.fmt("23.10") == "23.10$"
    assert P.fmt(Decimal("4.90")) == "4.90$"
