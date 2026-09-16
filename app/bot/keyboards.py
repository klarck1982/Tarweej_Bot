"""كل الأزرار في مكان واحد — مطابقة لخريطة الأزرار (خريطة_أزرار_البوت.html).

نوعان:
- ReplyKeyboard: اللوحة الرئيسية الثابتة أسفل الشاشة (H1).
- InlineKeyboard: الأزرار تحت الرسالة، تحمل callback_data قصيرة بالشكل  فرع:فعل:قيمة
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts as T
from app.config import settings


def ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


# ───────────── H1 اللوحة الرئيسية ─────────────

def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=T.BTN_META), KeyboardButton(text=T.BTN_TG)],
        [KeyboardButton(text=T.BTN_DESIGN), KeyboardButton(text=T.BTN_ORDERS)],
        [KeyboardButton(text=T.BTN_BALANCE), KeyboardButton(text=T.BTN_SUPPORT)],
        [KeyboardButton(text=T.BTN_INFO)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=T.BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="اختر من القائمة 👇")


# ───────────── H0 الترحيب ─────────────

def welcome() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📜 الشروط باختصار", "nav:terms")],
        [ib("✅ موافق، لنبدأ", "nav:accept")],
    ])


def terms_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("✅ موافق، لنبدأ", "nav:accept")]])


def home_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("🏠 القائمة", "nav:home")]])


def back(to: str, label: str = "◀️ رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib(label, to)]])


# ───────────── M0 باقات Meta (معاينة) ─────────────

def meta_packages(enabled: bool = True) -> InlineKeyboardMarkup:
    lock = "" if enabled else " 🔒"
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"🚀 تجربة — 14$ (2$ × 5 أيام){lock}", "meta:pkg:trial")],
        [ib(f"📈 نمو — 28$ (3$ × 7 أيام){lock}", "meta:pkg:growth")],
        [ib(f"💼 احتراف — 65$ (5$ × 10 أيام){lock}", "meta:pkg:pro")],
        [ib(f"🛠️ إعلان مخصص{lock}", "meta:pkg:custom"), ib(f"📦 انطلاقة متجر — 39${lock}", "meta:pkg:bundle")],
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
        [ib(f"✍️ نص إعلاني — 5${lock}", "add:svc:copy"), ib(f"🖼️ تصميم صورة — 8${lock}", "add:svc:design")],
        [ib(f"🎬 ريل من صورك — 15${lock}", "add:svc:reel"), ib(f"🎞️ مونتاج فيديو — 25${lock}", "add:svc:montage")],
        [ib(f"📦 باقات موفّرة{lock}", "add:svc:bundles")],
        [ib("🤖 ريل سينمائي AI" + (" — جديد ✨" if ai_on else " — قريباً 🔒"), "add:svc:ai_reel")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── B الرصيد ─────────────

def balance_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("➕ شحن رصيد", "bal:topup"), ib("📜 سجل العمليات", "bal:hist:1")],
        [ib("🏠 القائمة", "nav:home")],
    ])


# ───────────── S الدعم ─────────────

def support_menu() -> InlineKeyboardMarkup:
    rows = [
        [ib("❓ أسئلة شائعة", "sup:faq"), ib("🎫 فتح تذكرة", "sup:new")],
        [ib("📂 تذاكري", "sup:mine")],
    ]
    if settings.support_username:
        rows[1].append(url_btn("👤 تواصل مباشر", f"https://t.me/{settings.support_username}"))
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
        [ib("◀️ رجوع للأسئلة", "sup:faq"), ib(T.FAQ_NOT_SOLVED, "sup:new")],
    ])


# ───────────── I المعلومات ─────────────

def info_menu() -> InlineKeyboardMarkup:
    rows = [
        [ib("💲 أسعار الإعلانات", "info:ads"), ib("💲 أسعار التصميم", "info:design")],
        [ib("🛠️ كيف يعمل البوت؟", "info:how"), ib("📜 الشروط", "info:terms")],
        [ib("✨ عن المنصة", "info:about")],
    ]
    if settings.updates_channel:
        rows[2].append(url_btn("📣 قناة التحديثات", settings.updates_channel))
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def info_back(start_cta: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if start_cta:
        rows.append([ib("🚀 ابدأ الآن — اشحن رصيد", "bal:topup")])
    rows.append([ib("◀️ رجوع", "info:menu"), ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── A0 الأدمن ─────────────

def admin_panel(topups: int = 0, tasks: int = 0, tickets: int = 0) -> InlineKeyboardMarkup:
    def n(x: int) -> str:
        return f" ({x})" if x else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"📥 شحن معلّق{n(topups)}", "adm:topups"), ib(f"🛠️ مهام يدوية{n(tasks)}", "adm:tasks")],
        [ib(f"🎫 تذاكر{n(tickets)}", "adm:tickets"), ib("📊 إحصائيات", "adm:stats")],
        [ib("📣 بث رسالة", "adm:bc"), ib("⚙️ إعدادات", "adm:settings")],
        [ib("👤 بحث عن مستخدم", "adm:find"), ib("🔄 تحديث", "adm:panel")],
    ])


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("◀️ رجوع للوحة", "adm:panel")]])
