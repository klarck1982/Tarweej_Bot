"""الاستهداف الجغرافي والديموغرافي — مطابق لمرجع Nour Ads API v1.5 (targeting.countries / gender / age).

- 22 دولة بأكوادها ISO كما يقبلها الـ API، مع الاسم العربي والعلم.
- محافظات سوريا الـ 14 بمفاتيحها الحرفية (damascus, rural_damascus, ...) — بقية الدول تُستهدف كاملة ("all")
  في هذا الإصدار؛ لاستهداف مدن محددة خارج سوريا يكتبها العميل في الوصف ومدير الحملة يطبّقها يدوياً.
"""

from __future__ import annotations

# (كود ISO، الاسم العربي، العلم) — الترتيب = ترتيب الظهور في البوت
COUNTRIES: tuple[tuple[str, str, str], ...] = (
    ("SY", "سوريا", "🇸🇾"),
    ("SA", "السعودية", "🇸🇦"),
    ("EG", "مصر", "🇪🇬"),
    ("AE", "الإمارات", "🇦🇪"),
    ("IQ", "العراق", "🇮🇶"),
    ("JO", "الأردن", "🇯🇴"),
    ("LB", "لبنان", "🇱🇧"),
    ("KW", "الكويت", "🇰🇼"),
    ("QA", "قطر", "🇶🇦"),
    ("BH", "البحرين", "🇧🇭"),
    ("OM", "عُمان", "🇴🇲"),
    ("PS", "فلسطين", "🇵🇸"),
    ("MA", "المغرب", "🇲🇦"),
    ("DZ", "الجزائر", "🇩🇿"),
    ("TN", "تونس", "🇹🇳"),
    ("LY", "ليبيا", "🇱🇾"),
    ("SD", "السودان", "🇸🇩"),
    ("YE", "اليمن", "🇾🇪"),
    ("MR", "موريتانيا", "🇲🇷"),
    ("SO", "الصومال", "🇸🇴"),
    ("DJ", "جيبوتي", "🇩🇯"),
    ("KM", "جزر القمر", "🇰🇲"),
)
COUNTRY_BY_CODE = {c: (name, flag) for c, name, flag in COUNTRIES}
MAIN_COUNTRIES = ("SY", "SA", "EG", "AE", "IQ", "JO", "LB", "KW")   # الصفحة الأولى
OTHER_COUNTRIES = tuple(c for c, _, _ in COUNTRIES if c not in MAIN_COUNTRIES)

# محافظات سوريا — المفتاح كما يريده الـ API، والاسم العربي
SY_PROVINCES: tuple[tuple[str, str], ...] = (
    ("damascus", "دمشق"),
    ("rural_damascus", "ريف دمشق"),
    ("aleppo", "حلب"),
    ("homs", "حمص"),
    ("hama", "حماة"),
    ("latakia", "اللاذقية"),
    ("tartus", "طرطوس"),
    ("idlib", "إدلب"),
    ("daraa", "درعا"),
    ("suwayda", "السويداء"),
    ("quneitra", "القنيطرة"),
    ("deir_ez_zor", "دير الزور"),
    ("raqqa", "الرقة"),
    ("hasakah", "الحسكة"),
)
PROVINCES: dict[str, tuple[tuple[str, str], ...]] = {"SY": SY_PROVINCES}
PROVINCE_NAME = {k: v for provs in PROVINCES.values() for k, v in provs}

GENDERS = (("all", "الجميع 👥"), ("male", "رجال 👨"), ("female", "نساء 👩"))
GENDER_NAME = dict(GENDERS)

# فئات عمرية جاهزة (حد نور/فيسبوك: 13–65)
AGE_PRESETS: tuple[tuple[int, int, str], ...] = (
    (18, 65, "الكل 18 – 65"),
    (18, 24, "شباب 18 – 24"),
    (25, 34, "25 – 34"),
    (35, 44, "35 – 44"),
    (45, 65, "45 – 65"),
    (18, 34, "18 – 34"),
    (25, 45, "25 – 45"),
)

PLATFORMS = (("facebook", "فيسبوك", "📘"), ("instagram", "إنستغرام", "📸"), ("both", "فيسبوك + إنستغرام", "📘📸"))
PLATFORM_NAME = {c: f"{e} {n}" for c, n, e in PLATFORMS}

# الأهداف كما يقبلها الـ API — مع شرح بكلمة للعميل
GOALS: tuple[tuple[str, str, str], ...] = (
    ("post_promotion", "ترويج منشور", "الأكثر استخداماً — يوصل منشورك لأكبر عدد ويجلب تفاعلاً"),
    ("messages", "رسائل", "يجلب محادثات واتساب/ماسنجر — الأفضل للبيع المباشر"),
    ("traffic", "زيارات لرابط", "يرسل الناس إلى موقعك أو صفحتك أو رابط الطلب"),
    ("engagement", "تفاعل", "إعجابات وتعليقات ومشاركات على المنشور"),
    ("reach", "وصول", "أكبر عدد من الأشخاص يشاهدون الإعلان مرة واحدة على الأقل"),
    ("video_views", "مشاهدات فيديو", "للريلز والفيديوهات — يزيد المشاهدات بأقل تكلفة"),
)
GOAL_NAME = {c: n for c, n, _ in GOALS}
GOAL_HINT = {c: h for c, _, h in GOALS}


def country_label(code: str) -> str:
    name, flag = COUNTRY_BY_CODE.get(code, (code, "🌍"))
    return f"{flag} {name}"


def provinces_label(code: str, provinces: list[str] | None) -> str:
    if not provinces or "all" in provinces:
        return f"كل {COUNTRY_BY_CODE.get(code, (code,))[0]}"
    names = [PROVINCE_NAME.get(p, p) for p in provinces]
    return "، ".join(names)


def age_label(age_min: int, age_max: int) -> str:
    return f"{age_min} – {age_max} سنة"


def validate_provinces(code: str, provinces: list[str]) -> list[str]:
    """يعيد قائمة نظيفة صالحة للـ API: ["all"] أو مفاتيح موجودة فعلاً."""
    if code not in COUNTRY_BY_CODE:
        raise ValueError("دولة غير مدعومة")
    if not provinces or "all" in provinces or code not in PROVINCES:
        return ["all"]
    valid = {k for k, _ in PROVINCES[code]}
    clean = [p for p in provinces if p in valid]
    return clean or ["all"]
