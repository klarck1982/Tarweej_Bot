"""جدول الأسعار — مصدر واحد للحقيقة.

الأرقام من مخطط المشروع (عمولة Nour Ads 10% + هامشنا).
في الخطوة 3 تصبح هذه القيم قابلة للتعديل من لوحة الأدمن (جدول settings)؛
الآن هي ثوابت تُستخدم في شاشات الأسعار والاختبارات.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

NOUR_FEE_PCT = Decimal("10")  # عمولة الشريك

D = Decimal


def money(x: Decimal | float | int | str) -> Decimal:
    return D(str(x)).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def fmt(x: Decimal | float | int | str) -> str:
    """14.00 → "14$"، 23.10 → "23.10$"، 4.90 → "4.90$" (خانتان دائماً إن وُجدت كسور)."""
    v = money(x)
    s = f"{v:.0f}" if v == v.to_integral_value() else f"{v:.2f}"
    return f"{s}$"


# ───────────── Meta (فيسبوك / إنستغرام) ─────────────

@dataclass(frozen=True)
class MetaPackage:
    code: str
    title: str
    emoji: str
    daily: Decimal
    days: int
    price: Decimal      # ما يدفعه العميل
    blurb: str

    @property
    def budget(self) -> Decimal:
        return money(self.daily * self.days)

    @property
    def cost(self) -> Decimal:  # ما ندفعه للشريك = الميزانية + 10%
        return money(self.budget * (1 + NOUR_FEE_PCT / 100))

    @property
    def margin(self) -> Decimal:
        return money(self.price - self.cost)


META_PACKAGES: tuple[MetaPackage, ...] = (
    MetaPackage("trial", "تجربة", "🚀", D("2"), 5, D("14"), "لأول إعلان — تختبر التفاعل بأقل مبلغ"),
    MetaPackage("growth", "نمو", "📈", D("3"), 7, D("28"), "الأنسب لأغلب المتاجر والصفحات"),
    MetaPackage("pro", "احتراف", "💼", D("5"), 10, D("65"), "لإطلاق منتج أو عرض قوي"),
)

META_MIN_DAILY = D("2")
META_MIN_FEE = D("3")


def meta_custom_price(daily: Decimal | int | float, days: int) -> tuple[Decimal, Decimal, Decimal]:
    """إعلان مخصص: يعيد (الميزانية، السعر للعميل، التكلفة لدينا).

    الشرائح: ×1.30 حتى 50$ • ×1.25 من 50 إلى 200$ • ×1.20 فوق 200$ — وحد أدنى للرسوم 3$.
    """
    daily = D(str(daily))
    if daily < META_MIN_DAILY:
        raise ValueError("الحد الأدنى للميزانية اليومية 2$")
    if not 1 <= days <= 30:
        raise ValueError("المدة بين 1 و 30 يوماً")
    budget = money(daily * days)
    if budget <= 50:
        mult = D("1.30")
    elif budget <= 200:
        mult = D("1.25")
    else:
        mult = D("1.20")
    price = money(budget * mult)
    if price - budget < META_MIN_FEE:
        price = money(budget + META_MIN_FEE)
    cost = money(budget * (1 + NOUR_FEE_PCT / 100))
    return budget, price, cost


BUNDLE_STORE_LAUNCH = {"code": "store_launch", "title": "انطلاقة متجر", "price": D("39"),
                       "includes": "إعلان «نمو» 7 أيام + نص إعلاني + تصميم صورة"}

# ───────────── تيليغرام ─────────────

TG_ADS_MULT = D("1.35")       # الإعلان الرسمي: الميزانية × 1.35
TG_ADS_MIN_BUDGET = D("10")
TG_POST_MULT = D("1.25")      # القنوات الشريكة: سعر القناة × 1.25
TG_POST_PIN_EXTRA = D("0.50")  # التثبيت +50%


def tg_ads_price(budget: Decimal | int | float) -> Decimal:
    b = D(str(budget))
    if b < TG_ADS_MIN_BUDGET:
        raise ValueError("الحد الأدنى لإعلان تيليغرام 10$")
    return money(b * TG_ADS_MULT)


def tg_post_price(channel_price: Decimal | int | float, pinned: bool = False) -> Decimal:
    p = money(D(str(channel_price)) * TG_POST_MULT)
    if pinned:
        p = money(p * (1 + TG_POST_PIN_EXTRA))
    return p


# ───────────── الإضافات (كتابة وتصميم) ─────────────

ADDONS = {
    "copy":    {"title": "نص إعلاني",     "emoji": "✍️", "price": D("5"),  "hours": 24},
    "design":  {"title": "تصميم صورة",    "emoji": "🖼️", "price": D("8"),  "hours": 24},
    "reel":    {"title": "ريل من صورك",   "emoji": "🎬", "price": D("15"), "hours": 48},
    "montage": {"title": "مونتاج فيديو",  "emoji": "🎞️", "price": D("25"), "hours": 72},
}
ADDON_VOICEOVER = D("5")  # تعليق صوتي عربي AI
ADDON_BUNDLES = (
    {"title": "نص + صورة", "price": D("11"), "was": D("13")},
    {"title": "نص + صورة + ريل", "price": D("24"), "was": D("28")},
    {"title": "نص + صورتان + مونتاج", "price": D("46"), "was": D("54")},
)
