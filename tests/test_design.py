"""اختبارات 🎨 التصميم (v0.8.0) — بدون قاعدة بيانات: الباقات وعناصرها، التسعير، الحد الأدنى للمواد، رسم التعديل، ترتيب الخطوات."""
from decimal import Decimal

from app.services import design as DS, pricing as P


def test_bundles_have_items_and_prices():
    b = P.ADDON_BUNDLES
    assert [x["price"] for x in b] == [Decimal("11.00"), Decimal("24.00"), Decimal("46.00")]
    assert [list(x["items"]) for x in b] == [["copy", "design"], ["copy", "design", "reel"], ["copy", "design", "reel", "montage"]]
    assert P.bundle_items_from_title("نص + صورتان + مونتاج") == ["copy", "design", "design", "montage"]
    # التوفير = مجموع الأفراد − سعر الباقة
    for x in b:
        assert Decimal(x["was"]) == sum(P.ADDONS[c]["price"] for c in x["items"])


def test_prices_single_bundle_voiceover():
    assert DS.compute_prices({"items": ["copy"]}) == (Decimal("5.00"), Decimal("5.00"), Decimal("0.00"))
    assert DS.compute_prices({"items": ["copy", "design", "reel"], "bundle_idx": 1})[1] == Decimal("24.00")
    # التعليق الصوتي يُحتسب فقط مع فيديو
    assert DS.compute_prices({"items": ["copy"], "extras": ["voiceover"]})[1] == Decimal("5.00")
    assert DS.compute_prices({"items": ["reel"], "extras": ["voiceover", "music"]})[1] == Decimal("20.00")
    assert DS.compute_prices({"items": ["copy", "design", "reel"], "bundle_idx": 1, "extras": ["voiceover"]})[1] == Decimal("29.00")


def test_media_rules_and_hours():
    assert DS.needs_media(["copy"]) is False and DS.needs_media(["design"]) is True
    assert DS.has_video(["copy", "design"]) is False and DS.has_video(["reel"]) is True
    assert DS.media_ok(["reel"], 5, 0) and DS.media_ok(["reel"], 0, 3) and not DS.media_ok(["reel"], 4, 2)
    assert DS.media_ok(["montage"], 0, 1) and not DS.media_ok(["montage"], 9, 0)
    assert DS.media_ok(["design"], 0, 0) and DS.media_ok(["copy"], 0, 0)
    assert DS.deliver_hours(["copy"]) == 24 and DS.deliver_hours(["copy", "design", "reel"]) == 48 and DS.deliver_hours(["montage"]) == 72


def test_revision_fee_and_free_rule():
    order = {"spec": {"price": "29.00"}, "price_usd": Decimal("37.70"), "revision_count": 0}
    assert DS.next_revision_is_free(order)
    assert DS.revision_fee(order) == Decimal("8.70")        # 30% من سعر الخدمة الأصلي لا من المجموع بعد الرسوم
    order["revision_count"] = 1
    assert not DS.next_revision_is_free(order)
    assert DS.pct_label(Decimal("30")) == "30" and DS.pct_label(Decimal("12.5")) == "12.5"


def test_build_spec_and_lines():
    d = {"items": ["copy", "design", "reel"], "bundle_idx": 1, "business": "restaurant", "message": " خصم  30% ",
         "media": [["photo", "a"], ["photo", "b"], ["video", "c"]], "brand": {"logo": "L", "colors": "أحمر"},
         "lang": "gulf", "tone": "hot", "extras": ["voiceover"], "notes": ""}
    spec = DS.build_spec(d)
    assert spec["kind"] == "design" and spec["deliver_hours"] == 48 and spec["notes"] is None
    assert (spec["media_photos"], spec["media_videos"]) == (2, 1) and spec["message"] == "خصم  30%"
    lines = DS.spec_lines(spec, lambda s: s)
    assert any("2 صورة + 1 فيديو" in ln for ln in lines) and any("تعليق صوتي" in ln for ln in lines)
    spec2 = DS.build_spec({"items": ["copy"], "extras": ["voiceover"], "message": "x"})
    assert spec2["extras"] == [] and spec2["title"] == "✍️ نص إعلاني"
