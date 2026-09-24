"""جدول الأسعار — مصدر واحد للحقيقة، قابل للتعديل من Cpanel بلا إعادة نشر.

قاعدة إعلانات فيسبوك/إنستغرام (اتفاق 17/09/2026):
    تكلفتنا عند نور  = ميزانية الإعلان × 1.10   (عمولة نور 10%)
    سعر العميل       = ميزانية الإعلان × 1.30
    ربحنا            = 20% من الميزانية — بلا تقريب وبلا حد أدنى.

كيف يعمل التعديل الحي:
    DEFAULTS  = الأرقام الافتراضية (المكتوبة هنا).
    settings["pricing"] = ما غيّره الأدمن من Cpanel (يُدمج فوق الافتراضيات).
    refresh() تُحمّل الدمج في الذاكرة عند الإقلاع وبعد كل حفظ وكل 5 دقائق من المجدول.
    بقية الكود يقرأ P.CLIENT_MULT و P.META_PACKAGES… كما كان — عبر __getattr__ (PEP 562) فتأتي القيم الحية.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

D = Decimal


def money(x: Decimal | float | int | str) -> Decimal:
    return D(str(x)).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def fmt(x: Decimal | float | int | str) -> str:
    """14.00 → "14$"، 23.10 → "23.10$"، 4.90 → "4.90$" (خانتان دائماً إن وُجدت كسور)."""
    v = money(x)
    s = f"{v:.0f}" if v == v.to_integral_value() else f"{v:.2f}"
    return f"{s}$"


# ═══════════════════════════ الافتراضيات ═══════════════════════════

DEFAULTS: dict[str, Any] = {
    "meta": {"mult": "1.30", "nour_fee_pct": "10", "min_daily": "2", "max_daily": "500",
             "min_days": 1, "max_days": 30, "both_min_daily": "4"},
    "packages": [
        {"code": "trial", "title": "تجربة", "emoji": "🚀", "daily": "2", "days": 5,
         "blurb": "لأول إعلان — تختبر التفاعل بأقل مبلغ", "enabled": True},
        {"code": "growth", "title": "نمو", "emoji": "📈", "daily": "3", "days": 7,
         "blurb": "الأنسب لأغلب المتاجر والصفحات", "enabled": True},
        {"code": "pro", "title": "احتراف", "emoji": "💼", "daily": "5", "days": 10,
         "blurb": "لإطلاق منتج أو عرض قوي", "enabled": True},
    ],
    # باقة «انطلاقة متجر»: إعلان نمو (27.30$) + نص إعلاني (5$) + تصميم صورة (8$) = 40.30$ متفرقة → 38$
    "bundle": {"code": "store_launch", "title": "انطلاقة متجر", "emoji": "📦", "price": "38",
               "package": "growth", "addons": ["copy", "design"],
               "includes": "إعلان «نمو» 7 أيام + نص إعلاني + تصميم صورة", "enabled": True},
    "tg_ads": {"mult": "1.35", "min": "10", "max": "500", "presets": [10, 20, 35, 50, 100]},
    "tg_post": {"mult": "1.25", "pin_extra": "0.50"},
    "addons": {
        "copy":    {"title": "نص إعلاني",    "emoji": "✍️", "price": "5",  "hours": 24},
        "design":  {"title": "تصميم صورة",   "emoji": "🖼️", "price": "8",  "hours": 24},
        "reel":    {"title": "ريل من صورك",  "emoji": "🎬", "price": "15", "hours": 48},
        "montage": {"title": "مونتاج فيديو", "emoji": "🎞️", "price": "25", "hours": 72},
    },
    "voiceover": "5",
    "addon_bundles": [
        {"title": "نص + صورة", "price": "11", "was": "13", "items": ["copy", "design"]},
        {"title": "نص + صورة + ريل", "price": "24", "was": "28", "items": ["copy", "design", "reel"]},
        {"title": "نص + صورة + ريل + مونتاج", "price": "46", "was": "53", "items": ["copy", "design", "reel", "montage"]},
    ],
}

# باقة قديمة حُفظت قبل v0.8.0 بأرقام غير متسقة (5+8+8+25 = 46 بلا توفير حقيقي) → تُرقّى تلقائياً إلى الباقة الجديدة
_LEGACY_BUNDLES = {("نص + صورتان + مونتاج", "46", "54"): DEFAULTS["addon_bundles"][2]}

ADDON_ORDER = ("copy", "design", "reel", "montage")
CORE_PACKAGES = ("trial", "growth", "pro")   # لا تُحذف — تُعطَّل فقط


@dataclass(frozen=True)
class MetaPackage:
    code: str
    title: str
    emoji: str
    daily: Decimal
    days: int
    blurb: str
    enabled: bool = True

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
        return self.daily >= _cfg["META_BOTH_MIN_DAILY"]


def merged(raw: dict | None) -> dict:
    """يدمج ما حفظه الأدمن فوق الافتراضيات (القوائم تُستبدل كاملة، القواميس تُدمج مفتاحاً مفتاحاً)."""
    out = copy.deepcopy(DEFAULTS)
    raw = raw or {}
    for key in ("meta", "tg_ads", "tg_post", "bundle"):
        if isinstance(raw.get(key), dict):
            out[key].update(raw[key])
    if isinstance(raw.get("addons"), dict):
        for code, a in raw["addons"].items():
            if isinstance(a, dict):
                out["addons"].setdefault(code, {}).update(a)
    for key in ("packages", "addon_bundles"):
        if isinstance(raw.get(key), list):
            out[key] = copy.deepcopy(raw[key])
    # v0.8.0: كل باقة إضافات تعرف محتوياتها (items) — النسخ المحفوظة قبل ذلك تُستكمل من الافتراضيات أو من العنوان
    for i, b in enumerate(out["addon_bundles"]):
        if not b.get("items") and (b.get("title"), str(b.get("price")), str(b.get("was"))) in _LEGACY_BUNDLES:
            out["addon_bundles"][i] = b = copy.deepcopy(_LEGACY_BUNDLES[(b.get("title"), str(b.get("price")), str(b.get("was")))])
        if not b.get("items"):
            dflt = DEFAULTS["addon_bundles"][i]["items"] if i < len(DEFAULTS["addon_bundles"]) and \
                DEFAULTS["addon_bundles"][i]["title"] == b.get("title") else None
            b["items"] = list(dflt or bundle_items_from_title(str(b.get("title") or "")))
    if raw.get("voiceover") is not None:
        out["voiceover"] = raw["voiceover"]
    return out


def bundle_items_from_title(title: str) -> list[str]:
    """يستنتج محتويات باقة إضافات من عنوانها («نص + صورتان + مونتاج» ← copy, design, design, montage)."""
    items: list[str] = []
    if "نص" in title:
        items.append("copy")
    if "صورتان" in title or "صورتين" in title:
        items += ["design", "design"]
    elif "صور" in title:
        items.append("design")
    if "ريل" in title:
        items.append("reel")
    if "مونتاج" in title:
        items.append("montage")
    return items or ["copy", "design"]


def _build(cfg: dict) -> dict[str, Any]:
    m = cfg["meta"]
    fee = D(str(m["nour_fee_pct"]))
    packages = tuple(MetaPackage(p["code"], p["title"], p["emoji"], D(str(p["daily"])), int(p["days"]),
                                 p.get("blurb", ""), bool(p.get("enabled", True))) for p in cfg["packages"])
    addons = {code: {**a, "price": D(str(a["price"])), "hours": int(a.get("hours", 24))} for code, a in cfg["addons"].items()}
    b = cfg["bundle"]
    bundle = {**b, "price": D(str(b["price"])), "addons": tuple(b.get("addons") or ()), "enabled": bool(b.get("enabled", True))}
    t = cfg["tg_ads"]
    return {
        "NOUR_FEE_PCT": fee, "NOUR_MULT": 1 + fee / 100, "CLIENT_MULT": D(str(m["mult"])),
        "META_MIN_DAILY": D(str(m["min_daily"])), "META_MAX_DAILY": D(str(m["max_daily"])),
        "META_MIN_DAYS": int(m["min_days"]), "META_MAX_DAYS": int(m["max_days"]),
        "META_BOTH_MIN_DAILY": D(str(m["both_min_daily"])),
        "META_PACKAGES": tuple(p for p in packages if p.enabled),
        "META_ALL_PACKAGES": packages,
        "META_BY_CODE": {p.code: p for p in packages},
        "BUNDLE_STORE_LAUNCH": bundle,
        "TG_ADS_MULT": D(str(t["mult"])), "TG_ADS_MIN_BUDGET": D(str(t["min"])), "TG_ADS_MAX_BUDGET": D(str(t["max"])),
        "TG_ADS_PRESETS": tuple(int(x) for x in t["presets"]),
        "TG_POST_MULT": D(str(cfg["tg_post"]["mult"])), "TG_POST_PIN_EXTRA": D(str(cfg["tg_post"]["pin_extra"])),
        "ADDONS": {c: addons[c] for c in ADDON_ORDER if c in addons} | {c: a for c, a in addons.items() if c not in ADDON_ORDER},
        "ADDON_VOICEOVER": D(str(cfg["voiceover"])),
        "ADDON_BUNDLES": tuple({**x, "price": D(str(x["price"])), "was": D(str(x["was"])),
                                "items": tuple(x.get("items") or bundle_items_from_title(str(x.get("title") or "")))}
                               for x in cfg["addon_bundles"]),
    }


_raw: dict = merged(None)
_cfg: dict[str, Any] = _build(_raw)


def __getattr__(name: str) -> Any:  # PEP 562 — P.CLIENT_MULT وأخواتها تأتي حيّة من الإعدادات
    try:
        return _cfg[name]
    except KeyError:
        raise AttributeError(name) from None


def current() -> dict:
    """النسخة الخام (قابلة للتحويل إلى JSON) — ما يعرضه Cpanel."""
    return copy.deepcopy(_raw)


def apply(raw: dict | None) -> None:
    """يفعّل إعدادات جديدة في الذاكرة فوراً (بعد التحقق منها بـ validate)."""
    global _raw, _cfg
    new_raw = merged(raw)
    _cfg = _build(new_raw)   # إن فشل البناء لا نلمس النسخة الحالية
    _raw = new_raw


async def refresh() -> None:
    """يقرأ settings["pricing"] من القاعدة (متجاوزاً الذاكرة المؤقتة) ويفعّله — يُستدعى عند الإقلاع وبعد كل حفظ."""
    from app.db.repo import settings as settings_repo
    settings_repo.invalidate("pricing")
    try:
        apply(await settings_repo.get("pricing", {}) or {})
    except Exception:  # noqa: BLE001 — إعدادات تالفة لا توقف البوت: نبقى على آخر نسخة سليمة
        pass


# ═══════════════════════════ التحقق (Cpanel) ═══════════════════════════

def _dec(v: Any, name: str, lo: Decimal, hi: Decimal) -> Decimal:
    try:
        d = money(D(str(v).replace(",", ".").strip()))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name}: رقم غير صالح") from None
    if not lo <= d <= hi:
        raise ValueError(f"{name}: يجب أن يكون بين {lo} و {hi}")
    return d


# v0.9.2: أي سعر خدمة مدفوعة ≥ 1$ — صفر بالخطأ في Cpanel كان يجعل الخدمة مجانية بلا تنبيه
MIN_PRICE = D("1")


def validate(raw: dict) -> dict:
    """يعيد نسخة نظيفة قابلة للحفظ أو يرمي ValueError برسالة عربية واضحة."""
    cfg = merged(raw)
    m = cfg["meta"]
    mult = _dec(m["mult"], "مضاعف Meta", D("1"), D("3"))
    fee = _dec(m["nour_fee_pct"], "عمولة Nour", D("0"), D("50"))
    if mult <= 1 + fee / 100:
        raise ValueError("مضاعف Meta يجب أن يكون أكبر من (1 + عمولة Nour) وإلا تخسر في كل طلب")
    min_daily = _dec(m["min_daily"], "أدنى ميزانية يومية", D("2"), D("100"))
    max_daily = _dec(m["max_daily"], "أقصى ميزانية يومية", min_daily, D("10000"))
    max_days = int(m["max_days"])
    if not 1 <= max_days <= 30:
        raise ValueError("أقصى مدة: بين 1 و 30 يوماً (حد Nour Ads)")
    both_min = _dec(m["both_min_daily"], "حد «كلاهما»", min_daily, max_daily)
    clean_meta = {"mult": str(mult), "nour_fee_pct": str(fee), "min_daily": str(min_daily), "max_daily": str(max_daily),
                  "min_days": 1, "max_days": max_days, "both_min_daily": str(both_min)}

    pkgs, codes = [], set()
    for i, p in enumerate(cfg["packages"], 1):
        code = str(p.get("code") or f"pkg{i}").strip().lower()
        if not code.replace("_", "").isalnum() or code in codes:
            raise ValueError(f"الباقة {i}: رمز مكرر أو غير صالح")
        codes.add(code)
        title = str(p.get("title") or "").strip()
        if not 1 <= len(title) <= 24:
            raise ValueError(f"الباقة {i}: الاسم مطلوب (حتى 24 حرفاً)")
        daily = _dec(p.get("daily"), f"باقة «{title}» — اليومي", min_daily, max_daily)
        days = int(p.get("days") or 0)
        if not 1 <= days <= max_days:
            raise ValueError(f"باقة «{title}»: الأيام بين 1 و {max_days}")
        pkgs.append({"code": code, "title": title, "emoji": str(p.get("emoji") or "📦")[:4], "daily": str(daily),
                     "days": days, "blurb": str(p.get("blurb") or "")[:80], "enabled": bool(p.get("enabled", True))})
    for core in CORE_PACKAGES:
        if core not in codes:
            raise ValueError(f"الباقة الأساسية «{core}» لا تُحذف — عطّلها فقط")
    if len(pkgs) > 6:
        raise ValueError("حتى 6 باقات كحد أقصى حتى تبقى الشاشة مريحة")

    b = cfg["bundle"]
    if b.get("package") not in codes:
        raise ValueError("باقة «انطلاقة متجر» تشير إلى باقة غير موجودة")
    clean_bundle = {**b, "title": str(b.get("title") or "انطلاقة متجر")[:24], "emoji": str(b.get("emoji") or "📦")[:4],
                    "price": str(_dec(b["price"], "سعر انطلاقة متجر", D("1"), D("10000"))),
                    "includes": str(b.get("includes") or "")[:120], "enabled": bool(b.get("enabled", True)),
                    "addons": [a for a in (b.get("addons") or []) if a in cfg["addons"]]}

    t = cfg["tg_ads"]
    t_mult = _dec(t["mult"], "مضاعف Telegram Ads", D("1"), D("3"))
    t_min = _dec(t["min"], "أدنى ميزانية تيليغرام", D("1"), D("1000"))
    t_max = _dec(t["max"], "أقصى ميزانية تيليغرام", t_min, D("100000"))
    presets = []
    for x in t.get("presets") or []:
        try:
            v = int(D(str(x)))
        except (InvalidOperation, ValueError):
            raise ValueError("الأرقام الجاهزة لتيليغرام: أعداد صحيحة فقط") from None
        if not t_min <= v <= t_max:
            raise ValueError(f"الرقم الجاهز {v}$ خارج الحدود ({t_min} – {t_max})")
        if v not in presets:
            presets.append(v)
    presets.sort()
    if not 1 <= len(presets) <= 8:
        raise ValueError("الأرقام الجاهزة لتيليغرام: من 1 إلى 8 أرقام")
    clean_tg = {"mult": str(t_mult), "min": str(t_min), "max": str(t_max), "presets": presets}

    tp = cfg["tg_post"]
    clean_tp = {"mult": str(_dec(tp["mult"], "مضاعف القنوات الشريكة", D("1"), D("3"))),
                "pin_extra": str(_dec(tp["pin_extra"], "زيادة التثبيت", D("0"), D("2")))}

    clean_addons = {}
    for code, a in cfg["addons"].items():
        hours = int(a.get("hours") or 24)
        if not 1 <= hours <= 240:
            raise ValueError(f"إضافة «{a.get('title', code)}»: مدة التسليم بين 1 و 240 ساعة")
        clean_addons[code] = {"title": str(a.get("title") or code)[:24], "emoji": str(a.get("emoji") or "✨")[:4],
                              "price": str(_dec(a["price"], f"سعر «{a.get('title', code)}»", MIN_PRICE, D("10000"))), "hours": hours}
    for core in ADDON_ORDER:
        if core not in clean_addons:
            raise ValueError(f"الإضافة الأساسية «{core}» لا تُحذف")

    clean_ab = []
    for x in cfg["addon_bundles"]:
        items = [c for c in (x.get("items") or []) if c in clean_addons] or bundle_items_from_title(str(x.get("title") or ""))
        if len(items) > 6:
            raise ValueError("باقة إضافات: حتى 6 عناصر")
        ab_price = _dec(x["price"], "سعر باقة إضافات", MIN_PRICE, D("10000"))
        ab_was = _dec(x["was"], "السعر قبل الخصم", ab_price, D("10000"))   # لا «خصم» يرفع السعر
        clean_ab.append({"title": str(x.get("title") or "")[:40], "price": str(ab_price), "was": str(ab_was), "items": items})

    clean = {"meta": clean_meta, "packages": pkgs, "bundle": clean_bundle, "tg_ads": clean_tg, "tg_post": clean_tp,
             "addons": clean_addons, "voiceover": str(_dec(cfg["voiceover"], "التعليق الصوتي", MIN_PRICE, D("1000"))),
             "addon_bundles": clean_ab}
    _build(clean)  # يجب أن يُبنى بلا أخطاء
    return clean


# ═══════════════════════════ Meta (فيسبوك / إنستغرام) ═══════════════════════════

def meta_cost(budget: Decimal) -> Decimal:
    """ما ندفعه لنور مقابل ميزانية إعلان معيّنة."""
    return money(D(str(budget)) * _cfg["NOUR_MULT"])


def meta_price(budget: Decimal) -> Decimal:
    """ما يدفعه العميل مقابل ميزانية إعلان معيّنة — حرفياً × المضاعف."""
    return money(D(str(budget)) * _cfg["CLIENT_MULT"])


def meta_custom_price(daily: Decimal | int | float | str, days: int) -> tuple[Decimal, Decimal, Decimal]:
    """إعلان مخصص: يعيد (الميزانية، السعر للعميل، التكلفة لدينا). نفس القاعدة بلا شرائح ولا حد أدنى."""
    daily = D(str(daily))
    if daily < _cfg["META_MIN_DAILY"]:
        raise ValueError(f"الحد الأدنى للميزانية اليومية {fmt(_cfg['META_MIN_DAILY'])}")
    if daily > _cfg["META_MAX_DAILY"]:
        raise ValueError(f"الحد الأقصى للميزانية اليومية {fmt(_cfg['META_MAX_DAILY'])} — للمبالغ الأكبر تواصل مع الدعم")
    if not _cfg["META_MIN_DAYS"] <= int(days) <= _cfg["META_MAX_DAYS"]:
        raise ValueError(f"المدة بين {_cfg['META_MIN_DAYS']} و {_cfg['META_MAX_DAYS']} يوماً")
    budget = money(daily * int(days))
    return budget, meta_price(budget), meta_cost(budget)


def cheapest_package() -> MetaPackage | None:
    pk = _cfg["META_PACKAGES"]
    return min(pk, key=lambda p: p.price) if pk else None


def bundle_enabled() -> bool:
    b = _cfg["BUNDLE_STORE_LAUNCH"]
    return bool(b["enabled"]) and b["package"] in _cfg["META_BY_CODE"]


def bundle_separate_total() -> Decimal:
    b = _cfg["BUNDLE_STORE_LAUNCH"]
    p = _cfg["META_BY_CODE"][b["package"]]
    return money(p.price + sum(_cfg["ADDONS"][a]["price"] for a in b["addons"] if a in _cfg["ADDONS"]))


# ═══════════════════════════ تيليغرام ═══════════════════════════

def tg_ads_price(budget: Decimal | int | float) -> Decimal:
    b = D(str(budget))
    if b < _cfg["TG_ADS_MIN_BUDGET"]:
        raise ValueError(f"الحد الأدنى لإعلان تيليغرام {fmt(_cfg['TG_ADS_MIN_BUDGET'])}")
    if b > _cfg["TG_ADS_MAX_BUDGET"]:
        raise ValueError(f"الحد الأقصى لإعلان تيليغرام {fmt(_cfg['TG_ADS_MAX_BUDGET'])}")
    return money(b * _cfg["TG_ADS_MULT"])


def tg_ads_quote(budget: Decimal | int | float, copy_addon: bool = False) -> tuple[Decimal, Decimal, Decimal]:
    """يعيد (الميزانية، سعر العميل، تكلفتنا) — التكلفة = الميزانية نفسها (تُصرف TON من حسابك بقيمتها)."""
    b = money(D(str(budget)))
    price = tg_ads_price(b)
    if copy_addon:
        price += _cfg["ADDONS"]["copy"]["price"]
    return b, money(price), b


def tg_post_price(channel_price: Decimal | int | float, pinned: bool = False) -> Decimal:
    p = money(D(str(channel_price)) * _cfg["TG_POST_MULT"])
    if pinned:
        p = money(p * (1 + _cfg["TG_POST_PIN_EXTRA"]))
    return p


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
