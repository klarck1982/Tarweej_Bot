"""طرق الدفع — التعريفات والقواعد المشتركة بين شاشات العميل ولوحة الأدمن.

الطرق الحالية (قرار 16/09):
- USDT على شبكتي TRC20 و BEP20  (kind = crypto)   → عنوان محفظة.
- شام كاش بالدولار وبالليرة السورية (kind = shamcash) → رقم حساب + اسم صاحب الحساب.
  الليرة تحتاج سعر صرف يضبطه الأدمن (settings.syp_per_usd) — بدونه تبقى الطريقة مخفية.

الرصيد داخل البوت دائماً بالدولار؛ الليرة تُحسب لحظة إنشاء الطلب وتُحفظ مع الطلب (amount_local + rate).
"""

from __future__ import annotations

import re
from decimal import ROUND_UP, Decimal, InvalidOperation

from app.db.repo import settings as settings_repo
from app.services.pricing import fmt

METHOD_ORDER = ["usdt_trc20", "usdt_bep20", "shamcash_usd", "shamcash_syp"]

DEFAULTS: dict[str, dict] = {
    "usdt_trc20": {"kind": "crypto", "title": "USDT — شبكة TRC20 (Tron)", "short": "USDT TRC20",
                   "network": "TRC20", "currency": "USDT", "enabled": True, "address": ""},
    "usdt_bep20": {"kind": "crypto", "title": "USDT — شبكة BEP20 (BNB Smart Chain)", "short": "USDT BEP20",
                   "network": "BEP20", "currency": "USDT", "enabled": True, "address": ""},
    "shamcash_usd": {"kind": "shamcash", "title": "شام كاش — دولار", "short": "شام كاش $",
                     "currency": "USD", "enabled": True, "address": "", "holder": ""},
    "shamcash_syp": {"kind": "shamcash", "title": "شام كاش — ليرة سورية", "short": "شام كاش ل.س",
                     "currency": "SYP", "enabled": True, "address": "", "holder": ""},
}
ICON = {"crypto": "💵", "shamcash": "🏦"}
KIND_NAME = {"crypto": "USDT", "shamcash": "شام كاش"}

# إثبات الدفع كنص: TxID للعملات الرقمية (طويل)، رقم العملية لشام كاش (قصير، أرقام غالباً)
_PROOF_RE = {
    "crypto": re.compile(r"^[0-9a-zA-Z]{20,128}$"),
    "shamcash": re.compile(r"^[0-9A-Za-z\-/]{4,40}$"),
}
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# ───────────── قراءة/حفظ ─────────────

async def get_methods() -> dict[str, dict]:
    """كل الطرق بالترتيب المعتمد، مع تعبئة الحقول الناقصة من الافتراضيات."""
    raw = await settings_repo.get("payment_methods", {}) or {}
    out: dict[str, dict] = {}
    for code in METHOD_ORDER + [c for c in raw if c not in METHOD_ORDER]:
        base = dict(DEFAULTS.get(code, {"kind": "crypto", "title": code, "currency": "USDT", "enabled": False, "address": ""}))
        base.update(raw.get(code, {}))
        out[code] = base
    return out


async def save_methods(methods: dict[str, dict]) -> None:
    await settings_repo.set_("payment_methods", methods)


async def syp_rate() -> Decimal:
    """كم ليرة سورية مقابل 1$ (يضبطه الأدمن). 0 = غير مضبوط."""
    try:
        return Decimal(str(await settings_repo.get("syp_per_usd", 0) or 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)


async def set_syp_rate(rate: Decimal) -> None:
    await settings_repo.set_("syp_per_usd", str(rate))


def needs_rate(m: dict) -> bool:
    return m.get("currency") == "SYP"


def is_ready(m: dict, rate: Decimal) -> bool:
    return bool(m.get("enabled") and m.get("address") and (not needs_rate(m) or rate > 0))


async def usable_methods() -> dict[str, dict]:
    """ما يراه العميل: مفعّلة + لها عنوان/حساب (+ سعر صرف لليرة)."""
    methods = await get_methods()
    rate = await syp_rate()
    return {c: m for c, m in methods.items() if is_ready(m, rate)}


def status_icon(m: dict, rate: Decimal) -> str:
    if not m.get("enabled"):
        return "🔴"
    return "🟢" if is_ready(m, rate) else "🟡"


def status_text(m: dict, rate: Decimal) -> str:
    if not m.get("enabled"):
        return "🔴 متوقفة"
    if not m.get("address"):
        return "🟡 بلا " + ("رقم حساب" if m["kind"] == "shamcash" else "عنوان")
    if needs_rate(m) and rate <= 0:
        return "🟡 بلا سعر صرف"
    return "🟢 فعّالة"


# ───────────── مبالغ وعرض ─────────────

def label(m: dict) -> str:
    return f"{ICON.get(m.get('kind'), '💳')} {m['title']}"


def syp_amount(usd: Decimal, rate: Decimal) -> Decimal:
    """المبلغ بالليرة مقرّباً للأعلى لأقرب 50 ل.س (أرقام نظيفة، ولا خسارة علينا)."""
    raw = (Decimal(usd) * rate) / 50
    return raw.quantize(Decimal("1"), rounding=ROUND_UP) * 50


def fmt_syp(x) -> str:
    return f"{int(Decimal(str(x))):,} ل.س"


def fmt_rate(rate: Decimal) -> str:
    rate = Decimal(str(rate))
    return f"{int(rate):,}" if rate == rate.to_integral_value() else f"{rate:,.2f}"


def pay_amount(m: dict, amount_usd, amount_local=None) -> str:
    """ما يجب تحويله فعلياً كما يُعرض للعميل والأدمن."""
    if m.get("currency") == "SYP" and amount_local is not None:
        return fmt_syp(amount_local)
    if m.get("currency") == "USDT":
        return f"{fmt(amount_usd)} USDT"
    return fmt(amount_usd)


def local_note(m: dict, row: dict) -> str:
    """ملحق يوضح المعادل بالليرة على بطاقة الأدمن: ' (56,250 ل.س @ 11,250)'."""
    if m.get("currency") == "SYP" and row.get("amount_local") is not None:
        return f" ({fmt_syp(row['amount_local'])} @ {fmt_rate(row['rate'] or 0)})"
    return ""


# ───────────── تحقق من الإدخال ─────────────

def proof_ok(m: dict, text: str) -> bool:
    return bool(_PROOF_RE.get(m.get("kind", "crypto"), _PROOF_RE["crypto"]).match(text.strip()))


def clean_address(m: dict, raw: str) -> str | None:
    """يعيد العنوان/رقم الحساب منظّفاً أو None إن كان غير صالح."""
    s = raw.strip().translate(_ARABIC_DIGITS)
    if m.get("kind") == "shamcash":
        s = s.replace(" ", "")
        return s if re.fullmatch(r"[0-9A-Za-z+_\-]{4,40}", s) else None
    return s if (20 <= len(s) <= 120 and " " not in s) else None


def parse_rate(raw: str) -> Decimal | None:
    s = raw.strip().translate(_ARABIC_DIGITS).replace(",", "").replace("،", "").replace(" ", "")
    try:
        v = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return v if 1 <= v <= 10_000_000 else None
