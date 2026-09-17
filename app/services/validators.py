"""تنظيف مدخلات العميل في معالج الطلب — الأرقام العربية، الواتساب، الروابط، المعرّفات."""

from __future__ import annotations

import re

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# رموز الدول التي يدعمها الشريك + الأكثر شيوعاً بين المغتربين — لاقتراح الرمز عند نسيانه
COUNTRY_DIAL = {
    "SY": "963", "SA": "966", "EG": "20", "AE": "971", "IQ": "964", "JO": "962", "LB": "961", "KW": "965",
    "QA": "974", "BH": "973", "OM": "968", "PS": "970", "MA": "212", "DZ": "213", "TN": "216", "LY": "218",
    "SD": "249", "YE": "967", "MR": "222", "SO": "252", "DJ": "253", "KM": "269", "TR": "90", "DE": "49",
}
_KNOWN_PREFIXES = tuple(sorted(COUNTRY_DIAL.values(), key=len, reverse=True))


def normalize_digits(s: str) -> str:
    return (s or "").translate(_AR_DIGITS)


def clean_whatsapp(raw: str, default_country: str = "SY") -> str | None:
    """يعيد رقماً دولياً بصيغة +9639xxxxxxxx أو None إن كان غير مفهوم.

    يقبل: +963 9xx xxx xxx · 00963… · 09xxxxxxxx (يُفترض سوريا) · 9639xxxxxxxx
    """
    s = normalize_digits(raw).strip()
    s = re.sub(r"[\s\-().]", "", s)
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = s[1:]
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            return None
        return "+" + digits
    if not s.isdigit():
        return None
    # محلي بصفر: 09xxxxxxxx → +963 9xxxxxxxx
    if s.startswith("0") and 9 <= len(s) <= 11:
        return "+" + COUNTRY_DIAL[default_country] + s[1:]
    # بلا + لكن يبدأ برمز دولة معروف
    if 10 <= len(s) <= 15 and s.startswith(_KNOWN_PREFIXES):
        return "+" + s
    return None


_URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[\w-]{2,}(/\S*)?$", re.IGNORECASE)


def clean_url(raw: str) -> str | None:
    s = (raw or "").strip()
    if " " in s or len(s) > 500:
        return None
    if not _URL_RE.match(s):
        return None
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s
    return s


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def clean_username(raw: str | None) -> str | None:
    s = (raw or "").strip().lstrip("@")
    if s.lower().startswith("https://t.me/") or s.lower().startswith("t.me/"):
        s = s.rsplit("/", 1)[-1]
    return s if _USERNAME_RE.match(s) else None


def parse_int(raw: str) -> int | None:
    s = normalize_digits(raw).strip().replace("$", "")
    try:
        return int(s)
    except ValueError:
        return None


def parse_decimal_str(raw: str) -> str | None:
    s = normalize_digits(raw).strip().replace("$", "").replace("،", ".").replace(",", ".")
    return s if re.match(r"^\d+(\.\d{1,2})?$", s) else None
