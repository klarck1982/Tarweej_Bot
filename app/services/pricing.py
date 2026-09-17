"""جدول الأسعار — مصدر واحد للحقيقة.

قاعدة إعلانات فيسبوك/إنستغرام (اتفاق 17/09/2026):
    تكلفتنا عند نور  = ميزانية الإعلان × 1.10   (عمولة نور 10%)
    سعر العميل       = ميزانية الإعلان × 1.30   (أغلى من نور للجمهور بـ 10 نقاط)
    ربحنا            = 20% من الميزانية — بلا تقريب وبلا حد أدنى.
    مثال: ميزانية 100$ ← ندفع لنور 110$ ← يدفع العميل 130$.

الأرقام هنا ثوابت؛ في الخطوة 6 تصبح قابلة للتعديل من لوحة الأدمن (جدول settings).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

NOUR_FEE_PCT = Decimal("10")        # عمولة الشريك فوق الميزانية
CLIENT_MULT = Decimal("1.30")       # سعر العميل = الميزانية × 1.30
NOUR_MULT = 1 + NOUR_FEE_PCT / 100  # 1.10

D = Decimal


def money(x: Decimal | float | int | str) -> Decimal:
    return D(str(x)).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def fmt(x: Decimal | float | int | str) -> str:
    """14.00 → "14$"، 23.10 → "23.10$"، 4.90 → "4.90$" (خانتان دائماً إن وُجدت كسور)."""
    v = money(x)
    s = f"{v:.0f}" if v == v.to_integral_value() else f"{v:.2f}"
    return f"{s}$"


# ───────────── Meta (فيسبوك / إنستغرام) ─────────────

META_MIN_DAILY = D("2")          # حد نور الأدنى لليوم الواحد
META_MAX_DAILY = D("500")
META_MIN_DAYS, META_MAX_DAYS = 1, 30
META_BOTH_MIN_DAILY = D("4")     # «كلاهما» = ميزانيتان ≥2$ لكل منصة


def meta_cost(budget: Decimal) -> Decimal:
    """ما ندفعه لنور مقابل ميزانية إعلان معيّنة."""
    return money(D(str(budget)) * NOUR_MULT)


def meta_price(budget: Decimal) -> Decimal:
    """ما يدفعه العميل مقابل ميزانية إعلان معيّنة — حرفياً × 1.30."""
    return money(D(str(budget)) * CLIENT_MULT)


@dataclass(frozen=True)
class MetaPackage:
    code: str
    title: str
    emoji: str
    daily: Decimal
    days: int
    blurb: str

    @property
    def budget(self) -> Decimal:
        return money(self.daily * self.days)

    @property
    def cost(self) -> Decimal:  # ما ندفعه للشريك
        return meta_cost(self.budget)

    @property
    def price(self) -> Decimal:  # ما يدفعه العميل
        return meta_price(self.budget)

    @property
    def margin(self) -> Decimal:
        return money(self.price - self.cost)

    @property
    def both_allowed(self) -> bool:
        return self.daily >= META_BOTH_MIN_DAILY


META_PACKAGES: tuple[MetaPackage, ...] = (
    MetaPackage("trial", "تجربة", "🚀", D("2"), 5, "لأول إعلان — تختبر التفاعل بأقل مبلغ"),
    MetaPackage("growth", "نمو", "📈", D("3"), 7, "الأنسب لأغلب المتاجر والصفحات"),
    MetaPackage("pro", "احتراف", "💼", D("5"), 10, "لإطلاق منتج أو عرض قوي"),
)
META_BY_CODE = {p.code: p for p in META_PACKAGES}


def meta_custom_price(daily: Decimal | int | float | str, days: int) -> tuple[Decimal, Decimal, Decimal]:
    """إعلان مخصص: يعيد (الميزانية، السعر للعميل، التكلفة لدينا). نفس القاعدة بلا شرائح ولا حد أدنى."""
    daily = D(str(daily))
    if daily < META_MIN_DAILY:
        raise ValueError(f"الحد الأدنى للميزانية اليومية {fmt(META_MIN_DAILY)}")
    if daily > META_MAX_DAILY:
        raise ValueError(f"الحد الأقصى للميزانية اليومية {fmt(META_MAX_DAILY)} — للمبالغ الأكبر تواصل مع الدعم")
    if not META_MIN_DAYS <= int(days) <= META_MAX_DAYS:
        raise ValueError(f"المدة بين {META_MIN_DAYS} و {META_MAX_DAYS} يوماً")
    budget = money(daily * int(days))
    return budget, meta_price(budget), meta_cost(budget)


# باقة «انطلاقة متجر»: إعلان نمو (27.30$) + نص إعلاني (5$) + تصميم صورة (8$) = 40.30$ متفرقة → 38$
BUNDLE_STORE_LAUNCH = {
    "code": "store_launch", "title": "انطلاقة متجر", "emoji": "📦", "price": D("38"),
    "package": "growth", "addons": ("copy", "design"),
    "includes": "إعلان «نمو» 7 أيام + نص إعلاني + تصميم صورة",
}


def bundle_separate_total() -> Decimal:
    p = META_BY_CODE[BUNDLE_STORE_LAUNCH["package"]]
    return money(p.price + sum(ADDONS[a]["price"] for a in BUNDLE_STORE_LAUNCH["addons"]))


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


def days_word(n: int) -> str:
    """صياغة عربية سليمة: يوم واحد / يومان / 3 أيام / 11 يوماً."""
    n = int(n)
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومان"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوماً"
