"""كل الأزرار في مكان واحد — مطابقة لخريطة الأزرار (خريطة_أزرار_البوت.html).

نوعان:
- ReplyKeyboard: اللوحة الرئيسية الثابتة أسفل الشاشة (H1).
- InlineKeyboard: الأزرار تحت الرسالة، تحمل callback_data قصيرة بالشكل  فرع:فعل:قيمة
"""

from __future__ import annotations

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts as T
from app.config import settings
from app.services import pricing as P
from app.services import targeting as TG


# ألوان الأزرار (Bot API 9.4): success أخضر · danger أحمر · primary أزرق · None رمادي
# لغة الألوان في البوت: أخضر = تأكيد/مال داخل · أحمر = إلغاء/رفض/إيقاف/إدارة · أزرق = الإجراء الرئيسي · رمادي = تنقّل
def ib(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


# ───────────── H1 القائمة الرئيسية ─────────────

def home_bar() -> ReplyKeyboardMarkup:
    """الشريط السفلي: زر واحد فقط 🏠 — القائمة نفسها ملوّنة داخل الرسالة."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=T.BTN_HOME)]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="اضغط 🏠 للقائمة الرئيسية",
    )


def main_menu(is_admin: bool = False, balance: str = "0$", attention: int = 0) -> InlineKeyboardMarkup:
    """القائمة الرئيسية — التصميم B (مدمج بنمط Ichancy، 7 أسطر بلا تمرير):

    زر عنوان عريض · فيسبوك/إنستغرام بعرض كامل (الأهم) · أزواج منطقية بنصف الشاشة لكل زر ·
    قناة العروض تظهر فقط إذا ضُبط UPDATES_CHANNEL · زر الإدارة يحمل عدّاد ما ينتظر الأدمن.
    """
    rows = [
        [ib(T.BTN_TITLE, "nav:title")],
        [ib(T.BTN_META, "nav:meta", "primary")],
        [ib(T.BTN_TG, "nav:tg"), ib(T.BTN_DESIGN, "nav:design")],
        [ib(f"{T.BTN_BALANCE} · {balance}", "bal:menu"), ib(T.BTN_TOPUP, "bal:topup", "success")],
        [ib(T.BTN_ORDERS, "nav:orders")],
        [ib(T.BTN_SUPPORT, "sup:menu"), ib(T.BTN_INFO, "info:menu")],
    ]
    if settings.updates_channel:
        rows.append([url_btn(f"{T.BTN_CHANNEL} ↗", settings.updates_channel)])
    if is_admin:
        label = T.BTN_ADMIN + (f" · 🔔 {attention} بانتظارك" if attention else "")
        rows.append([ib(label, "adm:panel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── H0 الترحيب ─────────────

def welcome() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📜 الشروط باختصار", "nav:terms")],
        [ib("✅ موافق، لنبدأ", "nav:accept", "success")],
    ])


def terms_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("✅ موافق، لنبدأ", "nav:accept", "success")]])


def home_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("🏠 القائمة", "nav:home")]])


def back(to: str, label: str = "◀️ رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib(label, to)]])


def orders_empty() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"🚀 ابدأ بإعلان تجربة — {P.fmt(P.META_BY_CODE['trial'].price)}", "nav:meta", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── M0 باقات Meta (معاينة) ─────────────

def meta_packages(enabled: bool = True, has_draft: bool = False) -> InlineKeyboardMarkup:
    lock = "" if enabled else " 🔒"
    rows = []
    if has_draft:
        rows.append([ib("📦 أكمل طلبي المعلّق", "ord:resume", "success")])
    for i, p in enumerate(P.META_PACKAGES):
        rows.append([ib(f"{p.emoji} {p.title} — {P.fmt(p.price)} ({P.fmt(p.daily)} × {P.days_word(p.days)}){lock}",
                        f"meta:pkg:{p.code}", "primary" if i == 0 else None)])
    rows.append([ib(f"🛠️ إعلان مخصص — أنت تحدد الميزانية والمدة{lock}", "meta:pkg:custom")])
    rows.append([ib(f"📦 {P.BUNDLE_STORE_LAUNCH['title']} — {P.fmt(P.BUNDLE_STORE_LAUNCH['price'])} (نمو + نص + صورة){lock}", "meta:pkg:bundle")])
    rows.append([ib("ℹ️ شو الفرق بين الباقات؟", "meta:diff")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_diff_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("◀️ رجوع للباقات", "meta:pkgs")]])


# ───────────── T0 تيليغرام ─────────────

def tg_tracks(ads_on: bool = True, post_on: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📣 إعلان Telegram Ads الرسمي" + ("" if ads_on else " 🔒"), "tga:start")],
        [ib("📝 نشر في قنوات شريكة" + ("" if post_on else " 🔒"), "tgp:start")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── D0 التصميم ─────────────

def design_services(enabled: bool = True, ai_on: bool = False) -> InlineKeyboardMarkup:
    lock = "" if enabled else " 🔒"
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"✍️ نص إعلاني — 5${lock}", "add:svc:copy")],
        [ib(f"🖼️ تصميم صورة — 8${lock}", "add:svc:design")],
        [ib(f"🎬 ريل من صورك — 15${lock}", "add:svc:reel")],
        [ib(f"🎞️ مونتاج فيديو — 25${lock}", "add:svc:montage")],
        [ib(f"📦 باقات موفّرة{lock}", "add:svc:bundles")],
        [ib("🤖 ريل سينمائي AI" + (" — جديد ✨" if ai_on else " — قريباً 🔒"), "add:svc:ai_reel")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── B الرصيد والشحن ─────────────

def balance_menu(pending_id: int | None = None, draft_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [[ib(T.BTN_TOPUP, "bal:topup", "success")], [ib("📜 سجل العمليات", "bal:hist:1")]]
    if draft_id:
        rows.insert(0, [ib(f"📦 أكمل طلبي المعلّق #ORD-{draft_id}", "ord:resume", "primary")])
    if pending_id:
        rows.insert(0, [ib(f"🟡 متابعة الشحن المعلّق #TOP-{pending_id}", f"bal:view:{pending_id}")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_methods(usable: dict[str, dict]) -> InlineKeyboardMarkup:
    """usable = الطرق الجاهزة فقط (من payments.usable_methods)."""
    from app.services import payments as PM
    rows = [[ib(PM.label(m), f"bal:m:{code}")] for code, m in usable.items()]
    rows.append([ib("◀️ رجوع", "bal:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_amounts(presets: list, suggested: float | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if suggested:
        b.row(ib(f"✨ {suggested:g}$ — لإكمال طلبك", f"bal:amt:{suggested:g}"))
    btns = [ib(f"{p:g}$", f"bal:amt:{p:g}") for p in presets]
    for i in range(0, len(btns), 3):
        b.row(*btns[i:i + 3])
    b.row(ib("❌ إلغاء", "bal:cancel", "danger"))
    b.row(ib("◀️ رجوع", "bal:topup"))
    return b.as_markup()


def topup_instructions(topup_id: int, address: str, copy_label: str = "📋 نسخ العنوان") -> InlineKeyboardMarkup:
    rows = [
        [ib("✅ حوّلت — أرسل الإثبات", f"bal:paid:{topup_id}", "success")],
        [InlineKeyboardButton(text=copy_label, copy_text=CopyTextButton(text=address), style="primary")],
        [ib("❌ إلغاء الطلب", f"bal:cancel:{topup_id}", "danger")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_proof(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("◀️ رجوع للتعليمات", f"bal:view:{topup_id}")],
        [ib("❌ إلغاء الطلب", f"bal:cancel:{topup_id}", "danger")],
    ])


def topup_waiting(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📎 إرسال إثبات إضافي", f"bal:paid:{topup_id}")],
        [ib("❌ إلغاء الطلب", f"bal:cancel:{topup_id}", "danger")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def topup_approved(has_draft: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if has_draft:
        rows.append([ib("📦 أكمل طلبي المعلّق", "ord:resume", "success")])
    rows.append([ib("🚀 ابدأ طلباً جديداً", "nav:home", "primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_rejected() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("🔁 إعادة المحاولة", "bal:topup", "primary")],
        [ib("🎫 فتح تذكرة", "sup:new")],
    ])


def history_nav(page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(ib("◀️", f"bal:hist:{page - 1}"))
        nav.append(ib(f"{page} / {pages}", "nav:noop"))
        if page < pages:
            nav.append(ib("▶️", f"bal:hist:{page + 1}"))
        rows.append(nav)
    rows.append([ib("◀️ رجوع", "bal:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── S الدعم ─────────────

def support_menu() -> InlineKeyboardMarkup:
    rows = [
        [ib("❓ أسئلة شائعة", "sup:faq")],
        [ib("🎫 فتح تذكرة", "sup:new")],
        [ib("📂 تذاكري", "sup:mine")],
    ]
    if settings.support_username:
        rows.append([url_btn("👤 تواصل مباشر", f"https://t.me/{settings.support_username}")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_list() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, (q, _) in enumerate(T.FAQ):
        b.row(ib(q, f"sup:faq:{i}"))
    b.row(ib("◀️ رجوع", "sup:menu"))
    return b.as_markup()


def faq_answer() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(T.FAQ_NOT_SOLVED, "sup:new")],
        [ib("◀️ رجوع للأسئلة", "sup:faq")],
    ])


# ───────────── I المعلومات ─────────────

def info_menu() -> InlineKeyboardMarkup:
    rows = [
        [ib("💲 أسعار الإعلانات", "info:ads")],
        [ib("💲 أسعار التصميم", "info:design")],
        [ib("🛠️ كيف يعمل البوت؟", "info:how")],
        [ib("📜 الشروط", "info:terms")],
        [ib("✨ عن المنصة", "info:about")],
    ]
    if settings.updates_channel:
        rows.append([url_btn("📣 قناة التحديثات", settings.updates_channel)])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def info_back(start_cta: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if start_cta:
        rows.append([ib("🚀 ابدأ الآن — اشحن رصيد", "bal:topup", "success")])
    rows.append([ib("◀️ رجوع", "info:menu")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── A0 الأدمن ─────────────

def admin_panel(topups: int = 0, tasks: int = 0, tickets: int = 0, orders: int = 0, attention: int = 0) -> InlineKeyboardMarkup:
    def n(x: int) -> str:
        return f" ({x})" if x else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"📥 شحن معلّق{n(topups)}", "adm:topups", "primary" if topups else None)],
        [ib(f"📦 الطلبات المفتوحة{n(orders)}" + (f" · 🔔 {attention}" if attention else ""), "adm:orders",
            "primary" if attention else None)],
        [ib(f"🛠️ مهام يدوية{n(tasks)}", "adm:tasks", "primary" if tasks else None)],
        [ib(f"🎫 تذاكر{n(tickets)}", "adm:tickets", "primary" if tickets else None)],
        [ib("📊 إحصائيات", "adm:stats")],
        [ib("📣 بث رسالة", "adm:bc")],
        [ib("⚙️ إعدادات", "adm:settings")],
        [ib("👤 بحث عن مستخدم", "adm:find")],
        [ib("🔄 تحديث", "adm:panel")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("◀️ رجوع للوحة", "adm:panel")]])


def admin_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("🔛 تشغيل / إيقاف الخدمات", "adm:svcs")],
        [ib("🏦 طرق الدفع والحسابات", "adm:wallets")],
        [ib("💱 سعر صرف الليرة", "adm:rate")],
        [ib("👤 المعرّف الاحتياطي (نور)", "adm:fallback")],
        [ib("📡 قنوات الإدارة", "adm:ch:menu")],
        [ib("◀️ رجوع للوحة", "adm:panel")],
    ])


# ═══════════════════════════ قنوات الإدارة ═══════════════════════════

def admin_channels_menu(cfg: dict) -> InlineKeyboardMarkup:
    from app.services import channels as CH
    rows = []
    for kind in CH.KINDS:
        ch = cfg.get(kind)
        if ch and ch.get("id"):
            rows.append([ib(f"{CH.label(kind)} — {ch.get('title', '')[:24]} ✅", f"adm:ch:info:{kind}")])
        else:
            rows.append([ib(f"{CH.label(kind)} — غير مربوطة", f"adm:ch:help")])
    rows.append([ib("❓ كيف أربط قناة؟", "adm:ch:help")])
    rows.append([ib("◀️ رجوع", "adm:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_channel_info(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("🧪 إرسال رسالة اختبار", f"adm:ch:test:{kind}")],
        [ib("🔌 فصل القناة", f"adm:ch:unbind:{kind}", "danger")],
        [ib("◀️ رجوع", "adm:ch:menu")],
    ])


def admin_channel_bind(chat_id: int, taken: dict) -> InlineKeyboardMarkup:
    """يظهر في خاصّ الأدمن فور إضافة البوت إلى قناة: لأي غرض تُستخدم؟"""
    from app.services import channels as CH
    rows = []
    for kind in CH.KINDS:
        cur = taken.get(kind)
        suffix = f" (حالياً: {cur['title'][:16]})" if cur and cur.get("id") else ""
        rows.append([ib(f"{CH.label(kind)}{suffix}", f"adm:ch:bind:{kind}:{chat_id}", "primary" if not cur else None)])
    rows.append([ib("🚫 تجاهل هذه القناة", f"adm:ch:ignore:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_topup_card(topup_id: int, has_proof_image: bool, remaining: int = 0, in_channel: bool = False) -> InlineKeyboardMarkup:
    rows = [[ib("✅ اعتماد", f"adm:top:{topup_id}:ok", "success"), ib("❌ رفض", f"adm:top:{topup_id}:no", "danger")],
            [ib("✏️ اعتماد بمبلغ مختلف", f"adm:top:{topup_id}:adj")]]
    if has_proof_image and not in_channel:   # في القناة الصورة ظاهرة في البطاقة نفسها
        rows.append([ib("👁️ فتح الإثبات", f"adm:top:{topup_id}:proof")])
    rows.append([ib("💬 مراسلة العميل", f"adm:msg:{topup_id}")])
    if in_channel:
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if remaining:
        rows.append([ib(f"⏭️ التالي ({remaining})", "adm:topups:next", "primary")])
    rows.append([ib("◀️ القائمة", "adm:topups")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_topup_list(rows_data: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for tid, label in rows_data:
        b.row(ib(label, f"adm:top:{tid}:view"))
    b.row(ib("🔄 تحديث", "adm:topups"))
    b.row(ib("◀️ رجوع للوحة", "adm:panel"))
    return b.as_markup()


def admin_reject_reasons(topup_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, label in T.REJECT_REASONS:
        b.row(ib(label, f"adm:top:{topup_id}:no:{code}"))
    b.row(ib("✍️ سبب آخر", f"adm:top:{topup_id}:no:custom"))
    b.row(ib("◀️ رجوع", f"adm:top:{topup_id}:kb"))   # يعيد أزرار البطاقة في مكانها (يعمل في القناة والخاص)
    return b.as_markup()


def admin_wallets(methods: dict[str, dict], rate) -> InlineKeyboardMarkup:
    from app.services import payments as PM
    b = InlineKeyboardBuilder()
    for code, m in methods.items():
        state = PM.status_icon(m, rate)
        style = {"🟢": "success", "🔴": "danger"}.get(state)
        b.row(ib(f"{state} {m['title']}", f"adm:wal:{code}", style))
    b.row(ib("💱 تعديل سعر الصرف", "adm:rate"))
    b.row(ib("◀️ رجوع", "adm:settings"))
    return b.as_markup()


def admin_wallet_edit(code: str, m: dict) -> InlineKeyboardMarkup:
    rows = []
    if m.get("kind") == "shamcash":
        rows.append([ib("✏️ تعديل رقم الحساب", f"adm:wal:{code}:edit")])
        rows.append([ib("👤 تعديل اسم صاحب الحساب", f"adm:wal:{code}:holder")])
        if m.get("currency") == "SYP":
            rows.append([ib("💱 تعديل سعر الصرف", "adm:rate")])
    else:
        rows.append([ib("✏️ تعديل العنوان", f"adm:wal:{code}:edit")])
    if m.get("enabled"):
        rows.append([ib("🔴 إيقاف الطريقة", f"adm:wal:{code}:toggle", "danger")])
    else:
        rows.append([ib("🟢 تفعيل الطريقة", f"adm:wal:{code}:toggle", "success")])
    rows.append([ib("◀️ رجوع", "adm:wallets")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_input(back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("❌ إلغاء", back_to, "danger")]])


# ═══════════════════════════ الخطوة 3 — معالج Meta (M0–M8) ═══════════════════════════

def _nav(back: str | None = None, cancel: str = "meta:cancel") -> list[list[InlineKeyboardButton]]:
    row = []
    if back:
        row.append(ib("◀️ رجوع", back))
    row.append(ib("❌ إلغاء", cancel, "danger"))
    return [row]


def meta_custom_daily(presets=(2, 3, 5, 10, 15, 20)) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    btns = [ib(f"{p}$ / يوم", f"meta:daily:{p}") for p in presets]
    for i in range(0, len(btns), 3):
        b.row(*btns[i:i + 3])
    for r in _nav("meta:pkgs"):
        b.row(*r)
    return b.as_markup()


def meta_custom_days(presets=(3, 5, 7, 10, 14, 30)) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    btns = [ib(P.days_word(p), f"meta:days:{p}") for p in presets]
    for i in range(0, len(btns), 3):
        b.row(*btns[i:i + 3])
    for r in _nav("meta:pkg:custom"):
        b.row(*r)
    return b.as_markup()


def meta_platform(both_allowed: bool, back: str) -> InlineKeyboardMarkup:
    rows = [
        [ib("📘 فيسبوك", "meta:plat:facebook", "primary")],
        [ib("📸 إنستغرام", "meta:plat:instagram")],
    ]
    if both_allowed:
        rows.append([ib("📘📸 كلاهما", "meta:plat:both")])
    rows += _nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_goal() -> InlineKeyboardMarkup:
    rows = [[ib(("⭐ " if i == 0 else "") + name, f"meta:goal:{code}", "primary" if i == 0 else None)]
            for i, (code, name, _) in enumerate(TG.GOALS)]
    rows += _nav("meta:back:platform")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_country(more: bool = False) -> InlineKeyboardMarkup:
    codes = TG.OTHER_COUNTRIES if more else TG.MAIN_COUNTRIES
    b = InlineKeyboardBuilder()
    btns = [ib(TG.country_label(c), f"meta:ctry:{c}", "primary" if c == "SY" and not more else None) for c in codes]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib("◀️ الدول الرئيسية", "meta:ctry_page:0") if more else ib("🌐 دول أخرى", "meta:ctry_page:1"))
    for r in _nav("meta:back:goal"):
        b.row(*r)
    return b.as_markup()


def meta_provinces(code: str, selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    provs = TG.PROVINCES.get(code, ())
    btns = [ib(("✅ " if k in selected else "") + name, f"meta:prov:{k}", "success" if k in selected else None)
            for k, name in provs]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib("✔️ تم — كل الدولة" if not selected else f"✔️ تم ({len(selected)} محافظات)", "meta:prov_done", "primary"))
    if selected:
        b.row(ib("🧹 مسح الاختيار", "meta:prov_clear"))
    for r in _nav("meta:back:country"):
        b.row(*r)
    return b.as_markup()


def meta_audience(gender: str, age_min: int, age_max: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(*[ib(("• " if g == gender else "") + name, f"meta:gender:{g}", "success" if g == gender else None)
            for g, name in TG.GENDERS])
    btns = []
    for lo, hi, label in TG.AGE_PRESETS:
        cur = (lo, hi) == (age_min, age_max)
        btns.append(ib(("• " if cur else "") + label, f"meta:age:{lo}:{hi}", "success" if cur else None))
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib("التالي ▶️", "meta:aud_done", "primary"))
    for r in _nav("meta:back:country"):
        b.row(*r)
    return b.as_markup()


def meta_text_step(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_nav(back))


def meta_media(n: int) -> InlineKeyboardMarkup:
    rows = [[ib("✔️ تم — التالي" if n else "⏭️ تخطّي — المنشور جاهز", "meta:media_done", "primary")]]
    if n:
        rows.append([ib("🧹 حذف المرفقات", "meta:media_clear")])
    rows += _nav("meta:back:desc")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_addon_copy(price: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"✍️ نعم، اكتبوا لي النص (+{price})", "meta:addon:copy:1", "success")],
        [ib("لا، عندي نصّي", "meta:addon:copy:0")],
    ] + _nav("meta:back:media"))


def meta_whatsapp_confirm(back: str = "meta:back:whatsapp") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ صحيح", "meta:wa_ok", "success")],
        [ib("✏️ تعديل الرقم", back)],
    ] + _nav(None))


def meta_username_missing() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ ضبطته — تحقق", "meta:uname_check", "success")],
        [ib("⏭️ متابعة بدون معرّف", "meta:uname_skip")],
    ] + _nav("meta:back:whatsapp"))


def meta_summary(price_ok: bool, price: str, gap: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if price_ok:
        rows.append([ib(f"✅ تأكيد الطلب — {price}", "meta:confirm", "success")])
    else:
        rows.append([ib(f"➕ اشحن {gap} وأكمل", "meta:topup_gap", "success")])
    rows.append([ib("✏️ تعديل", "meta:edit")])
    rows.append([ib("❌ إلغاء", "meta:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_edit_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📍 المنصة", "meta:back:platform"), ib("🎯 الهدف", "meta:back:goal")],
        [ib("🌍 الدولة", "meta:back:country"), ib("👥 الجمهور", "meta:back:audience")],
        [ib("🔗 الرابط", "meta:back:link"), ib("📝 الوصف", "meta:back:desc")],
        [ib("🖼️ الملفات", "meta:back:media"), ib("📱 الواتساب", "meta:back:whatsapp")],
        [ib("◀️ رجوع للملخص", "meta:back:summary")],
    ])


def meta_done(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📦 متابعة الطلب", f"ord:view:{order_id}", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def meta_draft_saved(order_id: int, gap: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"➕ اشحن {gap} الآن", "meta:topup_gap", "success")],
        [ib("🗑️ إلغاء المسودة", f"ord:draft_cancel:{order_id}", "danger")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── O — طلباتي ─────────────

def orders_list(rows_data: list[tuple[int, str]], page: int, pages: int, has_draft_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_draft_id:
        b.row(ib("📦 أكمل طلبي المعلّق", "ord:resume", "success"))
    for oid, label in rows_data:
        b.row(ib(label, f"ord:view:{oid}"))
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(ib("◀️", f"ord:list:{page - 1}"))
        nav.append(ib(f"{page} / {pages}", "nav:noop"))
        if page < pages:
            nav.append(ib("▶️", f"ord:list:{page + 1}"))
        b.row(*nav)
    b.row(ib("🚀 طلب جديد", "nav:meta", "primary"))
    b.row(ib("🏠 القائمة", "nav:home"))
    return b.as_markup()


def order_view(order: dict) -> InlineKeyboardMarkup:
    st = order["status"]
    rows = []
    if st == "awaiting_payment":
        rows.append([ib("📦 أكمل الطلب", "ord:resume", "success")])
        rows.append([ib("🗑️ إلغاء المسودة", f"ord:draft_cancel:{order['id']}", "danger")])
    if st in ("completed", "rejected", "failed_submit", "refunded"):
        rows.append([ib("🔁 إعادة الطلب بنفس الإعدادات", f"ord:renew:{order['id']}", "primary")])
    if order.get("media_count"):
        rows.append([ib("🖼️ عرض ملفاتي", f"ord:media:{order['id']}")])
    rows.append([ib("💬 مساعدة بهذا الطلب", f"ord:help:{order['id']}")])
    rows.append([ib("◀️ طلباتي", "ord:list:1"), ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── الأدمن — الطلبات ─────────────

def admin_orders_list(rows_data: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for oid, label in rows_data:
        b.row(ib(label, f"adm:ord:{oid}:view"))
    b.row(ib("🔄 تحديث", "adm:orders"))
    b.row(ib("◀️ رجوع للوحة", "adm:panel"))
    return b.as_markup()


def admin_order_card(order: dict, dry_run: bool, media_count: int = 0, in_channel: bool = False) -> InlineKeyboardMarkup:
    oid = order["id"]
    st = order["status"]
    rows = []
    if st == "paid":
        rows.append([ib("📨 إعادة الإرسال لنور الآن", f"adm:ord:{oid}:submit", "primary")])
    if st in ("submitted", "in_progress", "active", "paused") and not dry_run:
        rows.append([ib("🔄 تحديث الحالة من نور", f"adm:ord:{oid}:sync")])
    if dry_run and st in ("submitted", "in_progress", "active", "paused"):
        nxt = {"submitted": [("🧪 ▶️ نور قَبِل (in_progress)", "in_progress"), ("🧪 ❌ نور رفض (استرداد)", "rejected")],
               "in_progress": [("🧪 🟢 الإعلان انطلق (active)", "active"), ("🧪 ❌ نور رفض (استرداد)", "rejected")],
               "active": [("🧪 ✅ اكتمل (completed)", "completed"), ("🧪 ⏸️ توقف مؤقتاً (paused)", "paused")],
               "paused": [("🧪 🟢 استُؤنف (active)", "active"), ("🧪 ✅ اكتمل (completed)", "completed")]}[st]
        for label, code in nxt:
            rows.append([ib(label, f"adm:ord:{oid}:sim:{code}")])
    if media_count:
        rows.append([ib(f"📎 ملفات العميل ({media_count})", f"adm:ord:{oid}:media")])
    rows.append([ib("💬 مراسلة العميل", f"adm:ord:{oid}:msg")])
    if st in ("paid", "submitted", "in_progress", "active", "paused"):
        rows.append([ib("↩️ استرداد كامل وإغلاق", f"adm:ord:{oid}:refund", "danger")])
    if not in_channel:
        rows.append([ib("◀️ الطلبات", "adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
