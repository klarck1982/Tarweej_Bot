"""💼 أزرار سوق القنوات. callback_data بالشكل mp:* (صاحب القناة) · mpc:* (العميل) · adm:mp:* (الأدمن) — كلها ≤ 64 بايت."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards import ib, url_btn
from app.db.repo import partner_channels as PC
from app.services import marketplace as MP


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def home_row():
    return [ib("🏠 القائمة", "nav:home")]


# ───────────── صاحب القناة ─────────────

def intro() -> InlineKeyboardMarkup:
    return _kb([[ib("➕ سجّل قناتي", "mp:add", "primary")], [ib("❓ كيف يعمل والشروط", "mp:how")], home_row()])


def dash(channels: list[dict], waiting: int) -> InlineKeyboardMarkup:
    rows = [[ib("📥 طلبات قنواتي" + (f" · 🔔 {waiting}" if waiting else ""), "mp:orders", "primary" if waiting else None)],
            [ib("💰 أرباحي", "mp:earn", "success")]]
    for ch in channels[:8]:
        icon = {"approved": "🟢" if ch["enabled"] else "⏸️", "pending": "🕐", "draft": "📝", "rejected": "❌",
                "suspended": "⛔"}.get(ch["mp_status"], "📢")
        rows.append([ib(f"{icon} {ch['title'][:30]}", f"mp:ch:{ch['id']}")])
    rows += [[ib("➕ أضف قناة", "mp:add"), ib("❓ كيف يعمل", "mp:how")], home_row()]
    return _kb(rows)


def back_home() -> InlineKeyboardMarkup:
    return _kb([[ib("◀️ رجوع", "mp:home")], home_row()])


def add(bot_username: str) -> InlineKeyboardMarkup:
    link = f"https://t.me/{bot_username}?startchannel&admin=post_messages+edit_messages+delete_messages"
    return _kb([[url_btn("➕ أضف البوت لقناتي ↗", link)], [ib("◀️ رجوع", "mp:home")]])


def categories(cid: int) -> InlineKeyboardMarkup:
    items = list(PC.CATEGORIES.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([ib(f"{e} {n}", f"mp:cat:{cid}:{code}") for code, (e, n) in items[i:i + 2]])
    return _kb(rows)


def skip(data: str, label: str = "⏭️ بدون") -> InlineKeyboardMarkup:
    return _kb([[ib(label, data)], [ib("✖️ إلغاء", "mp:home")]])


def cancel() -> InlineKeyboardMarkup:
    return _kb([[ib("✖️ إلغاء", "mp:home")]])


def review(cid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("✅ أوافق وأرسل للمراجعة", f"mp:submit:{cid}", "success")],
                [ib("💵 تعديل الأسعار", f"mp:prices:{cid}"), ib("🗂 الفئة", f"mp:cats:{cid}")],
                [ib("📝 الوصف", f"mp:blurb:{cid}"), ib("🗑️ حذف", f"mp:del:{cid}", "danger")],
                [ib("◀️ رجوع", "mp:home")]])


def channel(ch: dict) -> InlineKeyboardMarkup:
    cid = ch["id"]
    rows = []
    if ch["mp_status"] in ("draft", "rejected"):
        return review(cid)
    if ch["mp_status"] == "approved":
        rows.append([ib("⏸️ إيقاف مؤقت" if ch["enabled"] else "▶️ تفعيل", f"mp:toggle:{cid}", None if ch["enabled"] else "success")])
    rows += [[ib("💵 الأسعار", f"mp:prices:{cid}"), ib("📝 الوصف", f"mp:blurb:{cid}")],
             [ib("🔄 تحديث عدد المشتركين", f"mp:resync:{cid}")],
             [url_btn("📢 فتح القناة ↗", ch["url"])],
             [ib("◀️ رجوع", "mp:home")]]
    return _kb(rows)


def orders(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[ib(label, f"mp:o:{oid}")] for oid, label in items]
    rows += [[ib("🔄 تحديث", "mp:orders"), ib("◀️ رجوع", "mp:home")]]
    return _kb(rows)


def request(oid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("✅ قبول واختيار الموعد", f"mp:o:{oid}:acc", "success")],
                [ib("❌ اعتذار", f"mp:o:{oid}:rej", "danger")],
                [ib("📥 كل طلباتي", "mp:orders")]])


def order(o: dict) -> InlineKeyboardMarkup:
    oid, st = o["id"], o["status"]
    rows = []
    if st == "submitted":
        return request(oid)
    if st == "in_progress":
        rows.append([ib("⚡ انشر الآن", f"mp:o:{oid}:now", "primary")])
        rows.append([ib("❌ اعتذار عن النشر", f"mp:o:{oid}:rej", "danger")])
    if o.get("post_url"):
        rows.append([url_btn("🔗 فتح المنشور ↗", o["post_url"])])
    rows.append([ib("◀️ طلباتي", "mp:orders"), ib("💼 لوحتي", "mp:home")])
    return _kb(rows)


def times(oid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("⚡ الآن", f"mp:o:{oid}:t:now", "primary")],
                [ib("⏰ بعد ساعة", f"mp:o:{oid}:t:1"), ib("⏰ بعد 3 ساعات", f"mp:o:{oid}:t:3")],
                [ib("🌙 الساعة 21:00", f"mp:o:{oid}:t:21"), ib("✍️ وقت آخر", f"mp:o:{oid}:t:x")],
                [ib("◀️ رجوع", f"mp:o:{oid}")]])


def reasons(oid: int) -> InlineKeyboardMarkup:
    rows = [[ib(txt, f"mp:o:{oid}:r:{code}")] for code, txt in MP.REJECT_REASONS.items()]
    rows += [[ib("✍️ سبب آخر", f"mp:o:{oid}:r:x")], [ib("◀️ رجوع", f"mp:o:{oid}")]]
    return _kb(rows)


def earn(can_withdraw: bool, can_convert: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_withdraw:
        rows.append([ib("💸 سحب الأرباح", "mp:wd", "success")])
    if can_convert:
        rows.append([ib("🔄 حوّل إلى رصيد إعلانات", "mp:cv", "primary")])
    rows += [[ib("📜 السجل", "mp:hist")], [ib("◀️ رجوع", "mp:home")]]
    return _kb(rows)


def wd_methods() -> InlineKeyboardMarkup:
    rows = [[ib(label, f"mp:wd:m:{code}")] for code, label in MP.PAYOUT_METHODS.items()]
    rows.append([ib("◀️ رجوع", "mp:earn")])
    return _kb(rows)


def wd_confirm(all_label: str) -> InlineKeyboardMarkup:
    return _kb([[ib(f"✅ تأكيد سحب {all_label}", "mp:wd:ok", "success")],
                [ib("✍️ مبلغ آخر", "mp:wd:amt")], [ib("✖️ إلغاء", "mp:earn")]])


def cv_confirm(all_label: str) -> InlineKeyboardMarkup:
    return _kb([[ib(f"✅ حوّل {all_label}", "mp:cv:ok", "success")],
                [ib("✍️ مبلغ آخر", "mp:cv:amt")], [ib("✖️ إلغاء", "mp:earn")]])


def to_earn() -> InlineKeyboardMarkup:
    return _kb([[ib("💰 أرباحي", "mp:earn")], [ib("💼 لوحتي", "mp:home")]])


# ───────────── العميل ─────────────

def rate(oid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("⭐" * n, f"mpc:rate:{oid}:{n}") for n in (1, 2, 3)],
                [ib("⭐" * n, f"mpc:rate:{oid}:{n}") for n in (4, 5)],
                [ib("📦 عرض الطلب", f"ord:view:{oid}")]])


def dispute_confirm(oid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("⚠️ نعم، أرسل البلاغ", f"mpc:dspok:{oid}", "danger")], [ib("◀️ رجوع", f"ord:view:{oid}")]])


# ───────────── الأدمن ─────────────

def adm_panel(c: dict, enabled: bool) -> InlineKeyboardMarkup:
    def n(x) -> str:
        return f" ({x})" if x else ""
    return _kb([
        [ib(f"🕐 قنوات بانتظار الموافقة{n(c['pending'])}", "adm:mp:list:pending", "primary" if c["pending"] else None)],
        [ib(f"💸 طلبات السحب{n(c['payouts'])}", "adm:mp:pays", "primary" if c["payouts"] else None)],
        [ib(f"⚠️ البلاغات{n(c['disputes'])}", "adm:mp:dsps", "danger" if c["disputes"] else None)],
        [ib("📋 كل قنوات السوق", "adm:mp:list:all")],
        [ib("⚙️ إعدادات السوق", "adm:mp:cfg"), ib("🔴 إيقاف السوق" if enabled else "🟢 تشغيل السوق", "adm:mp:onoff")],
        [ib("🔄 تحديث", "adm:mp"), ib("◀️ اللوحة", "adm:panel")],
    ])


def adm_list(items: list[tuple[str, str]], back: str = "adm:mp") -> InlineKeyboardMarkup:
    rows = [[ib(label, data)] for data, label in items]
    rows.append([ib("◀️ رجوع", back)])
    return _kb(rows)


def adm_channel(ch: dict) -> InlineKeyboardMarkup:
    cid = ch["id"]
    rows = []
    if ch["mp_status"] == "pending":
        rows.append([ib("✅ اعتماد", f"adm:mp:ok:{cid}", "success"), ib("❌ رفض", f"adm:mp:no:{cid}", "danger")])
    elif ch["mp_status"] == "approved":
        rows.append([ib("⛔ إيقاف القناة", f"adm:mp:sus:{cid}", "danger")])
    elif ch["mp_status"] == "suspended":
        rows.append([ib("▶️ إعادة التفعيل", f"adm:mp:res:{cid}", "success")])
    rows.append([url_btn("📢 فتح القناة ↗", ch["url"])])
    rows.append([ib("◀️ السوق", "adm:mp")])
    return _kb(rows)


def adm_payout(pid: int, pending: bool) -> InlineKeyboardMarkup:
    rows = []
    if pending:
        rows.append([ib("✅ تم الدفع", f"adm:mp:paid:{pid}", "success"), ib("❌ رفض وإرجاع", f"adm:mp:payno:{pid}", "danger")])
    rows.append([ib("◀️ السوق", "adm:mp")])
    return _kb(rows)


def adm_dispute(oid: int) -> InlineKeyboardMarkup:
    return _kb([[ib("↩️ استرداد للعميل", f"adm:mp:dspr:{oid}", "danger")],
                [ib("✅ رفض البلاغ (الربح للقناة)", f"adm:mp:dspk:{oid}", "success")],
                [ib("◀️ السوق", "adm:mp")]])


def adm_cfg() -> InlineKeyboardMarkup:
    from app.bot import mp_texts as TX
    rows = [[ib(f"✏️ {label}", f"adm:mp:set:{key}")] for key, label in TX.CFG_LABELS.items()]
    rows.append([ib("◀️ السوق", "adm:mp")])
    return _kb(rows)


def adm_cancel(back: str = "adm:mp") -> InlineKeyboardMarkup:
    return _kb([[ib("✖️ إلغاء", back)]])
