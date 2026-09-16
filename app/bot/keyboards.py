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


def main_menu(is_admin: bool = False, balance: str = "0$") -> InlineKeyboardMarkup:
    """القائمة الرئيسية داخل الرسالة (تصميم Ichancy): أزرق للخدمة الرئيسية، أخضر للشحن، أحمر للإدارة."""
    # قاعدة التصميم: زر واحد في كل سطر = عرض الشاشة كاملاً (لا يبدو كقائمة جانبية على الهاتف)
    rows = [
        [ib(T.BTN_META, "nav:meta", "primary")],
        [ib(T.BTN_TG, "nav:tg")],
        [ib(T.BTN_DESIGN, "nav:design")],
        [ib(T.BTN_TOPUP, "bal:topup", "success")],
        [ib(f"{T.BTN_BALANCE} · {balance}", "bal:menu")],
        [ib(T.BTN_ORDERS, "nav:orders")],
        [ib(T.BTN_SUPPORT, "sup:menu")],
        [ib(T.BTN_INFO, "info:menu")],
    ]
    if is_admin:
        rows.append([ib(T.BTN_ADMIN, "adm:panel", "danger")])
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
        [ib("🚀 ابدأ بإعلان تجربة — 14$", "nav:meta", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── M0 باقات Meta (معاينة) ─────────────

def meta_packages(enabled: bool = True) -> InlineKeyboardMarkup:
    lock = "" if enabled else " 🔒"
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"🚀 تجربة — 14$ (2$ × 5 أيام){lock}", "meta:pkg:trial")],
        [ib(f"📈 نمو — 28$ (3$ × 7 أيام){lock}", "meta:pkg:growth")],
        [ib(f"💼 احتراف — 65$ (5$ × 10 أيام){lock}", "meta:pkg:pro")],
        [ib(f"🛠️ إعلان مخصص{lock}", "meta:pkg:custom")],
        [ib(f"📦 انطلاقة متجر — 39${lock}", "meta:pkg:bundle")],
        [ib("ℹ️ شو الفرق بين الباقات؟", "meta:diff")],
        [ib("🏠 القائمة", "nav:home")],
    ])


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

def balance_menu(pending_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [[ib(T.BTN_TOPUP, "bal:topup", "success")], [ib("📜 سجل العمليات", "bal:hist:1")]]
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

def admin_panel(topups: int = 0, tasks: int = 0, tickets: int = 0) -> InlineKeyboardMarkup:
    def n(x: int) -> str:
        return f" ({x})" if x else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"📥 شحن معلّق{n(topups)}", "adm:topups", "primary" if topups else None)],
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
        [ib("◀️ رجوع للوحة", "adm:panel")],
    ])


def admin_topup_card(topup_id: int, has_proof_image: bool, remaining: int = 0) -> InlineKeyboardMarkup:
    rows = [[ib("✅ اعتماد", f"adm:top:{topup_id}:ok", "success"), ib("❌ رفض", f"adm:top:{topup_id}:no", "danger")],
            [ib("✏️ اعتماد بمبلغ مختلف", f"adm:top:{topup_id}:adj")]]
    if has_proof_image:
        rows.append([ib("👁️ فتح الإثبات", f"adm:top:{topup_id}:proof")])
    rows.append([ib("💬 مراسلة العميل", f"adm:msg:{topup_id}")])
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
    b.row(ib("◀️ رجوع للبطاقة", f"adm:top:{topup_id}:view"))
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
