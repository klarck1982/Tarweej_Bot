"""كل الأزرار في مكان واحد — مطابقة لخريطة الأزرار (خريطة_أزرار_البوت.html).

نوعان:
- ReplyKeyboard: اللوحة الرئيسية الثابتة أسفل الشاشة (H1).
- InlineKeyboard: الأزرار تحت الرسالة، تحمل callback_data قصيرة بالشكل  فرع:فعل:قيمة
"""

from __future__ import annotations

from aiogram.types import (
    WebAppInfo,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts as T
from app.services import cpanel as CP
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


def main_menu(is_admin: bool = False, balance: str = "0$", attention: int = 0,
              services: dict | None = None) -> InlineKeyboardMarkup:
    """القائمة الرئيسية — التصميم B (مدمج بنمط Ichancy، 7 أسطر بلا تمرير):

    زر عنوان عريض · فيسبوك/إنستغرام بعرض كامل (الأهم) · أزواج منطقية بنصف الشاشة لكل زر ·
    قناة العروض تظهر فقط إذا ضُبط UPDATES_CHANNEL · زر الإدارة يحمل عدّاد ما ينتظر الأدمن.
    """
    svc = services or {}
    hide = CP.rt("disabled_style") == "hide"

    def on(k: str) -> bool:
        return not hide or svc.get(k, True)

    rows = [[ib(T.BTN_TITLE, "nav:title")]]
    if on("meta"):
        rows.append([ib(T.BTN_META, "nav:meta", "primary")])
    pair = [ib(T.BTN_TG, "nav:tg")] if (on("tg_ads") or on("tg_post")) else []
    if on("addons") or on("ai_reel"):
        pair.append(ib(T.BTN_DESIGN, "nav:design"))
    if on("scheduled"):
        pair.append(ib("📅 تصميم يومي", "sub:list", "primary"))
    if pair:
        rows.append(pair)
    rows += [
        [ib(f"{T.BTN_BALANCE} · {balance}", "bal:menu"), ib(T.BTN_TOPUP, "bal:topup", "success")],
        [ib(T.BTN_ORDERS, "nav:orders")],
        [ib(T.BTN_SUPPORT, "sup:menu"), ib(T.BTN_INFO, "info:menu")],
    ]
    if CP.rt("updates_channel"):
        rows.append([url_btn(f"{T.BTN_CHANNEL} ↗", CP.rt("updates_channel"))])
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
        [ib(f"🚀 ابدأ بإعلان تجربة — {P.fmt(P.cheapest_package().price)}" if P.cheapest_package() else "🚀 ابدأ بأول إعلان", "nav:meta", "primary")],
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
    rows.append([ib(f"✏️ ميزانيتي بنفسي — اكتب المبلغ والأيام{lock}", "meta:pkg:custom")])
    if P.bundle_enabled():
        bp = P.META_BY_CODE[P.BUNDLE_STORE_LAUNCH["package"]]
        rows.append([ib(f"📦 {P.BUNDLE_STORE_LAUNCH['title']} — {P.fmt(P.BUNDLE_STORE_LAUNCH['price'])} ({bp.title} + نص + صورة){lock}", "meta:pkg:bundle")])
    rows.append([ib("ℹ️ شو الفرق بين الباقات؟", "meta:diff")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meta_diff_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("◀️ رجوع للباقات", "meta:pkgs")]])


# ───────────── T0 تيليغرام ─────────────

def tg_tracks(ads_on: bool = True, post_on: bool = True, channels_n: int = 0) -> InlineKeyboardMarkup:
    """المسار الشريك يُفتح عندما توجد قناة حيّة واحدة على الأقل والخدمة مفعّلة — وإلا يبقى «قريباً 🔒»."""
    post_live = post_on and channels_n > 0
    if post_live:
        post_btn = ib(f"📝 نشر في قنوات شريكة · {channels_n} {'قناة' if channels_n < 11 else 'قناة'}", "tgp:start", "success")
    else:
        post_btn = ib("📝 نشر في قنوات شريكة — قريباً 🔒", "tgp:start")
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📣 إعلان Telegram Ads الرسمي" + ("" if ads_on else " 🔒"), "tga:start", "primary" if ads_on else None)],
        [post_btn],
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
        [ib("📅 باقات تصميم يومي", "sub:list", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def scheduled_package_list(packages: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in packages:
        price = P.fmt(p.get("price_usd", 0))
        rows.append([ib(f"🎨 {p.get('title', 'باقة')} — {price} · {p.get('total_items', 0)} يوم", f"sub:pkg:{p.get('code', '')}", "primary")])
    rows.append([ib("📂 اشتراكاتي", "sub:mine")])
    rows.append([ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scheduled_package_detail(package: dict) -> InlineKeyboardMarkup:
    code = package.get("code", "")
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ اشترك الآن", f"sub:buy:{code}", "success")],
        [ib("◀️ رجوع للباقات", "sub:list")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def scheduled_purchase_confirm(code: str) -> InlineKeyboardMarkup:
    # رمز شراء لمرة واحدة في الزر نفسه (v0.9.2): الضغطة المكررة/الزر القديم لا يشتريان باقة ثانية.
    # الطول: 12 + رمز الباقة (≤40) + 1 + 8 = 61 بايت ≤ 64
    import secrets
    tok = secrets.token_hex(4)
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ تأكيد الدفع والاشتراك", f"sub:confirm:{code}:{tok}", "success")],
        [ib("💰 شحن الرصيد", "bal:topup")],
        [ib("◀️ رجوع", f"sub:pkg:{code}")],
    ])


def scheduled_after_purchase() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📂 اشتراكاتي", "sub:mine")],
        [ib("🎨 باقات أخرى", "sub:list")],
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


def topup_amounts(presets: list, suggested: float | None = None, min_label: str = "5$", max_label: str = "1000$") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if suggested:
        b.row(ib(f"✨ {suggested:g}$ — لإكمال طلبك", f"bal:amt:{suggested:g}"))
    btns = [ib(f"{p:g}$", f"bal:amt:{p:g}") for p in presets]
    for i in range(0, len(btns), 3):
        b.row(*btns[i:i + 3])
    b.row(ib(f"✏️ اكتب مبلغاً آخر ({min_label} – {max_label})", "bal:amt:type"))
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
    if CP.rt("support_username"):
        rows.append([url_btn("👤 تواصل مباشر", f"https://t.me/{CP.rt('support_username')}")])
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
    if CP.rt("updates_channel"):
        rows.append([url_btn("📣 قناة التحديثات", CP.rt("updates_channel"))])
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

def admin_panel(topups: int = 0, tasks: int = 0, tickets: int = 0, orders: int = 0, attention: int = 0,
                cpanel_url: str | None = None, late: int = 0) -> InlineKeyboardMarkup:
    def n(x: int) -> str:
        return f" ({x})" if x else ""
    rows = []
    if cpanel_url:
        rows.append([InlineKeyboardButton(text="🖥️ فتح Cpanel ↗", web_app=WebAppInfo(url=cpanel_url), style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows + [
        [ib(f"📥 شحن معلّق{n(topups)}", "adm:topups", "primary" if topups else None)],
        [ib(f"📦 الطلبات المفتوحة{n(orders)}" + (f" · 🔔 {attention}" if attention else ""), "adm:orders",
            "primary" if attention else None)],
        [ib(f"🛠️ مهام يدوية{n(tasks)}" + (f" · 🔴 {late}" if late else ""), "adm:tasks", "danger" if late else ("primary" if tasks else None))],
        [ib(f"🎫 تذاكر{n(tickets)}", "adm:tickets", "primary" if tickets else None)],
        [ib("📊 إحصائيات", "adm:stats")],
        [ib("📣 بث رسالة", "adm:bc")],
        [ib("⚙️ إعدادات سريعة", "adm:settings")],
        [ib("👤 بحث عن مستخدم", "adm:find")],
        [ib("🔄 تحديث", "adm:panel")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def cpanel_open(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖥️ فتح Cpanel ↗", web_app=WebAppInfo(url=url), style="primary")],
        [ib("🛠️ لوحة الأزرار السريعة", "adm:panel")],
    ])


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib("◀️ رجوع للوحة", "adm:panel")]])


def admin_scheduled_subscriber(subscription_id: int, cpanel_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[ib("📤 إضافة محتوى عبر البوت", f"adm:sub:{subscription_id}:add", "primary")]]
    if cpanel_url:
        rows.append([InlineKeyboardButton(text="🖥️ إدارة من Cpanel ↗", web_app=WebAppInfo(url=cpanel_url), style="primary")])
    rows.append([ib("📊 لوحة الإدارة", "adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("🔛 تشغيل / إيقاف الخدمات", "adm:svcs")],
        [ib("🏦 طرق الدفع والحسابات", "adm:wallets")],
        [ib("💱 سعر صرف الليرة", "adm:rate")],
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
            rows.append([ib(f"{CH.label(kind)} — غير مربوطة", "adm:ch:help")])
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
    presets = tuple(p for p in presets if P.META_MIN_DAILY <= p <= P.META_MAX_DAILY) or (int(P.META_MIN_DAILY),)
    b = InlineKeyboardBuilder()
    btns = [ib(f"{p}$ / يوم", f"meta:daily:{p}") for p in presets]
    for i in range(0, len(btns), 3):
        b.row(*btns[i:i + 3])
    b.row(ib(f"✏️ اكتب مبلغاً آخر (من {P.fmt(P.META_MIN_DAILY)}/يوم)", "meta:daily:type"))
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


# ═══════════════════════════ 📣 Telegram Ads — المعالج ═══════════════════════════

def _tga_nav(back: str | None = None) -> list[list[InlineKeyboardButton]]:
    return _nav(back, cancel="tga:cancel")


def tga_budget() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    presets = P.TG_ADS_PRESETS
    star = presets[1] if len(presets) > 1 else presets[0]   # الخيار المقترح = الثاني
    btns = [ib(f"{p}$ → {P.fmt(P.tg_ads_price(p))}", f"tga:budget:{p}", "primary" if p == star else None) for p in presets]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib(f"✏️ اكتب مبلغاً آخر ({P.fmt(P.TG_ADS_MIN_BUDGET)} – {P.fmt(P.TG_ADS_MAX_BUDGET)})", "tga:budget:type"))
    for r in _tga_nav("nav:tg"):
        b.row(*r)
    return b.as_markup()


def tga_target_mode() -> InlineKeyboardMarkup:
    rows = [[ib(TG.TGA_MODES["channels"], "tga:mode:channels", "primary")],
            [ib(TG.TGA_MODES["interests"], "tga:mode:interests")],
            [ib(TG.TGA_MODES["geo"], "tga:mode:geo")],
            [ib(TG.TGA_MODES["expert"], "tga:mode:expert")]]
    rows += _tga_nav("tga:back:budget")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tga_interests(selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    btns = [ib(("✅ " if k in selected else "") + name, f"tga:int:{k}", "success" if k in selected else None)
            for k, name in TG.TGA_INTERESTS]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib(f"✔️ تم ({len(selected)})" if selected else "✔️ تم — اختر واحداً على الأقل", "tga:int_done", "primary"))
    for r in _tga_nav("tga:back:mode"):
        b.row(*r)
    return b.as_markup()


def tga_country(more: bool = False) -> InlineKeyboardMarkup:
    codes = TG.OTHER_COUNTRIES if more else TG.MAIN_COUNTRIES
    b = InlineKeyboardBuilder()
    btns = [ib(TG.country_label(c), f"tga:ctry:{c}", "primary" if c == "SY" and not more else None) for c in codes]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(ib("🌍 كل الدول (عربي)", "tga:ctry:any"))
    b.row(ib("◀️ الدول الرئيسية", "tga:ctry_page:0") if more else ib("🌐 دول أخرى", "tga:ctry_page:1"))
    for r in _tga_nav("tga:back:mode"):
        b.row(*r)
    return b.as_markup()


def tga_lang() -> InlineKeyboardMarkup:
    rows = [[ib(name, f"tga:lang:{k}", "primary" if k == "ar" else None)] for k, name in TG.TGA_LANGS]
    rows += _tga_nav("tga:back:country")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tga_text_step(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_tga_nav(back))


def tga_text_input(copy_price: str) -> InlineKeyboardMarkup:
    rows = [[ib(f"✍️ اكتبولي النص (+{copy_price})", "tga:addon:copy", "success")]]
    rows += _tga_nav("tga:back:mode")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tga_summary(price_ok: bool, price: str, gap: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if price_ok:
        rows.append([ib(f"✅ تأكيد الطلب — {price}", "tga:confirm", "success")])
    else:
        rows.append([ib(f"➕ اشحن {gap} وأكمل", "meta:topup_gap", "success")])
    rows.append([ib("✏️ تعديل", "tga:edit")])
    rows.append([ib("❌ إلغاء", "tga:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tga_edit_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("💵 الميزانية", "tga:back:budget"), ib("🎯 الاستهداف", "tga:back:mode")],
        [ib("📝 النص", "tga:back:text"), ib("🔗 الرابط", "tga:back:link")],
        [ib("◀️ رجوع للملخص", "tga:back:summary")],
    ])


def tga_revision(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✍️ إرسال نص جديد", f"tga:revise:{order_id}", "primary")],
        [ib("💬 مساعدة بهذا الطلب", f"ord:help:{order_id}")],
        [ib("◀️ طلباتي", "ord:list:1"), ib("🏠 القائمة", "nav:home")],
    ])


def admin_tga_card(order: dict, in_channel: bool = False) -> InlineKeyboardMarkup:
    oid, st = order["id"], order["status"]
    rows = []
    nxt = {
        "submitted":      [("▶️ أنشأته في Telegram Ads", "in_progress", "primary"), ("✏️ اطلب تعديل النص", "needs_revision", None), ("❌ رفض تيليغرام (استرداد)", "rejected", "danger")],
        "in_progress":    [("🟢 وافق تيليغرام — انطلق", "active", "primary"), ("✏️ اطلب تعديل النص", "needs_revision", None), ("❌ رفض تيليغرام (استرداد)", "rejected", "danger")],
        "needs_revision": [("❌ رفض نهائي (استرداد)", "rejected", "danger")],
        "active":         [("✅ اكتمل — أدخل النتائج", "completed", "success"), ("⏸️ توقف مؤقتاً", "paused", None)],
        "paused":         [("🟢 استُؤنف", "active", "primary"), ("✅ اكتمل — أدخل النتائج", "completed", "success")],
    }.get(st, [])
    spec = order.get("spec") or {}
    if st in ("submitted", "needs_revision") and "copy" in (spec.get("addons") or []) and not spec.get("text"):
        rows.append([ib("✍️ أدخل النص الذي كتبته", f"adm:tga:{oid}:text", "success")])
    for label, code, style in nxt:
        rows.append([ib(label, f"adm:tga:{oid}:to:{code}", style)])
    rows.append([ib("💬 مراسلة العميل", f"adm:ord:{oid}:msg")])
    if st in ("submitted", "in_progress", "needs_revision", "active", "paused"):
        rows.append([ib("↩️ استرداد كامل وإغلاق", f"adm:ord:{oid}:refund", "danger")])
    if not in_channel:
        rows.append([ib("◀️ الطلبات", "adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════ 📝 القنوات الشريكة — المعالج ═══════════════════════════

def _tgp_nav(back: str | None = None) -> list[list[InlineKeyboardButton]]:
    return _nav(back, cancel="tgp:cancel")


def tgp_categories(cats: list[tuple[str, int]], total: int) -> InlineKeyboardMarkup:
    from app.db.repo import partner_channels as PC
    rows = [[ib(f"{PC.cat_label(c)} · {n}", f"tgp:cat:{c}")] for c, n in cats]
    if len(cats) > 1:
        rows.append([ib(f"📋 كل القنوات · {total}", "tgp:cat:all", "primary")])
    rows += _tgp_nav("nav:tg")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_channels(items: list[tuple[int, str]], cat: str, page: int, pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cid, label in items:
        b.row(ib(label, f"tgp:ch:{cid}"))
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(ib("◀️", f"tgp:cat:{cat}:{page - 1}"))
        nav.append(ib(f"{page} / {pages}", "nav:noop"))
        if page < pages:
            nav.append(ib("▶️", f"tgp:cat:{cat}:{page + 1}"))
        b.row(*nav)
    for r in _tgp_nav("tgp:back:cats"):
        b.row(*r)
    return b.as_markup()


def tgp_channel_card(cid: int, url: str, back_cat: str) -> InlineKeyboardMarkup:
    rows = []
    if url and url.startswith("https://"):
        rows.append([url_btn("👁️ عرض القناة ↗", url)])
    rows.append([ib("✅ اختيار هذه القناة", f"tgp:pick:{cid}", "success")])
    rows += _tgp_nav(f"tgp:cat:{back_cat}")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_formats(formats: list[tuple[str, str]], back_cid: int) -> InlineKeyboardMarkup:
    """formats = [(code, label_with_price)]"""
    rows = [[ib(label, f"tgp:fmt:{code}", "primary" if i == 0 else None)] for i, (code, label) in enumerate(formats)]
    rows += _tgp_nav(f"tgp:ch:{back_cid}")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_content(copy_price: str, has_copy: bool) -> InlineKeyboardMarkup:
    rows = []
    if not has_copy:
        rows.append([ib(f"✍️ اكتبولي النص (+{copy_price})", "tgp:addon:copy", "success")])
    rows += _tgp_nav("tgp:back:fmt")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_content_next(can_next: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_next:
        rows.append([ib("✅ تم — التالي", "tgp:content:next", "success")])
    rows.append([ib("🗑️ ابدأ المحتوى من جديد", "tgp:content:reset")])
    rows += _tgp_nav("tgp:back:fmt")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_when() -> InlineKeyboardMarkup:
    rows = [[ib("⚡ أقرب وقت متاح", "tgp:when:asap", "primary")],
            [ib("📅 وقت محدد ✍️", "tgp:when:type")]]
    rows += _tgp_nav("tgp:back:content")
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_summary(price_ok: bool, price: str, gap: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if price_ok:
        rows.append([ib(f"✅ تأكيد ودفع {price}", "tgp:confirm", "success")])
    else:
        rows.append([ib(f"➕ اشحن {gap} وأكمل", "meta:topup_gap", "success")])
    rows.append([ib("✏️ تعديل خطوة", "tgp:edit")])
    rows.append([ib("❌ إلغاء", "tgp:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_edit_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📢 القناة", "tgp:back:cats"), ib("🕐 الصيغة", "tgp:back:fmt")],
        [ib("✍️ المحتوى", "tgp:back:content"), ib("📅 الموعد", "tgp:back:when")],
        [ib("◀️ رجوع للملخص", "tgp:back:summary")],
    ])


def tgp_done(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📦 متابعة الطلب", f"ord:view:{order_id}", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def tgp_order_view(order: dict) -> InlineKeyboardMarkup:
    """بطاقة طلب النشر عند العميل — تختلف عن order_view بزر فتح المنشور والإلغاء المجاني."""
    st = order["status"]
    oid = order["id"]
    rows = []
    if st == "awaiting_payment":
        rows.append([ib("📦 أكمل الطلب", "ord:resume", "success")])
        rows.append([ib("🗑️ إلغاء المسودة", f"ord:draft_cancel:{oid}", "danger")])
    if order.get("post_url"):
        rows.append([url_btn("🔗 فتح المنشور ↗", order["post_url"])])
    if st == "submitted":
        rows.append([ib("🚫 إلغاء واسترداد (مجاني قبل الجدولة)", f"tgp:cancel_order:{oid}", "danger")])
    if st in ("completed", "rejected", "cancelled", "refunded"):
        rows.append([ib("🔁 كرّر في قناة أخرى", "tgp:start", "primary")])
    if order.get("media_count"):
        rows.append([ib("🖼️ عرض ملفاتي", f"ord:media:{oid}")])
    rows.append([ib("💬 مساعدة بهذا الطلب", f"ord:help:{oid}")])
    rows.append([ib("◀️ طلباتي", "ord:list:1"), ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tgp_cancel_confirm(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ نعم، ألغِ واسترد", f"tgp:cancel_yes:{order_id}", "danger")],
        [ib("◀️ رجوع", f"ord:view:{order_id}")],
    ])


def admin_tgp_card(order: dict, media_count: int = 0, in_channel: bool = False) -> InlineKeyboardMarkup:
    oid, st = order["id"], order["status"]
    spec = order.get("spec") or {}
    rows = []
    if st in ("submitted", "in_progress") and "copy" in (spec.get("addons") or []) and not spec.get("text"):
        rows.append([ib("✍️ أدخل النص الذي كتبته", f"adm:tgp:{oid}:text", "success")])
    if st in ("submitted", "in_progress"):
        rows.append([ib("📅 تأكيد الموعد ✍️" if st == "submitted" else "📅 تغيير الموعد ✍️", f"adm:tgp:{oid}:when", "primary")])
        rows.append([ib("🔗 تم النشر — ألصق الرابط ✍️", f"adm:tgp:{oid}:url", "success")])
    if st == "active":
        rows.append([ib("👁️ إدخال المشاهدات ✍️", f"adm:tgp:{oid}:views"), ib("✅ إنهاء الآن", f"adm:tgp:{oid}:finish", "success")])
    if st == "completed":
        rows.append([ib("👁️ تعديل المشاهدات ✍️", f"adm:tgp:{oid}:views")])
    if media_count:
        rows.append([ib(f"📎 ملفات العميل ({media_count})", f"adm:ord:{oid}:media")])
    rows.append([ib("💬 مراسلة العميل", f"adm:ord:{oid}:msg")])
    if st in ("submitted", "in_progress", "active"):
        rows.append([ib("❌ تعذّر النشر — استرداد كامل", f"adm:tgp:{oid}:reject", "danger")])
    if not in_channel:
        rows.append([ib("◀️ الطلبات", "adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═══════════════════════════ الخطوة 6 — 🎨 معالج التصميم (D0–D9) ═══════════════════════════

def _ds_nav(back: str | None = None) -> list[list[InlineKeyboardButton]]:
    return _nav(back, cancel="ds:cancel")


def ds_bundles(bundles: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[ib(label, f"ds:bundle:{i}", "primary")] for i, label in bundles]
    rows.append([ib("◀️ رجوع", "nav:design")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_business(items: tuple, back: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in items:
        b.add(ib(name, f"ds:biz:{code}"))
    b.adjust(2)
    for row in _ds_nav(back):
        b.row(*row)
    return b.as_markup()


def ds_message_step(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_ds_nav(back))


def ds_media(n: int, enough: bool, back: str) -> InlineKeyboardMarkup:
    rows = []
    if n and enough:
        rows.append([ib("✅ تم — أرسلت كل شي", "ds:media:done", "success")])
    elif n:
        rows.append([ib("✅ تم — أكمِلوا بما أرسلت", "ds:media:done", "success")])
    rows.append([ib("🚫 ما عندي مواد — استخدموا صوراً جاهزة", "ds:media:none")])
    if n:
        rows.append([ib("🗑️ حذف المرفقات", "ds:media:clear")])
    rows += _ds_nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_brand(saved: bool, got: bool, back: str) -> InlineKeyboardMarkup:
    rows = []
    if got:
        rows.append([ib("✅ تم — التالي", "ds:brand:done", "success")])
    if saved and not got:
        rows.append([ib("✅ استخدم هويتي المحفوظة", "ds:brand:saved", "success")])
    rows.append([ib("⏭️ ما في لوغو — اختاروا أنتم", "ds:brand:skip")])
    rows += _ds_nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_lang(langs: tuple, tones: tuple, lang: str, tone: str, back: str) -> InlineKeyboardMarkup:
    rows = []
    row = [ib(("✅ " if code == lang else "") + name, f"ds:lang:{code}", "primary" if code == lang else None) for code, name in langs]
    rows.append(row[:2])
    rows.append(row[2:])
    rows.append([ib(("✅ " if code == tone else "") + name, f"ds:tone:{code}", "primary" if code == tone else None) for code, name in tones])
    rows.append([ib("✅ التالي", "ds:lang:done", "success")])
    rows += _ds_nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_extras(extras: tuple, selected: list[str], vo_price: str, back: str) -> InlineKeyboardMarkup:
    rows = []
    for code, name, paid in extras:
        on = code in selected
        label = ("✅ " if on else "☐ ") + name + (f" +{vo_price}" if paid else "")
        rows.append([ib(label, f"ds:extra:{code}", "success" if on else None)])
    rows.append([ib("✅ تم", "ds:extras:done", "primary")])
    rows += _ds_nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_notes(back: str) -> InlineKeyboardMarkup:
    rows = [[ib("⏭️ بلا ملاحظات — إلى الملخص", "ds:notes:skip", "primary")]]
    rows += _ds_nav(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_summary(price_ok: bool, price: str, gap: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if price_ok:
        rows.append([ib(f"✅ تأكيد ودفع {price}", "ds:confirm", "success")])
    else:
        rows.append([ib(f"➕ اشحن {gap} وأكمل", "meta:topup_gap", "success")])
    rows.append([ib("✏️ تعديل خطوة", "ds:edit")])
    rows.append([ib("❌ إلغاء", "ds:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_edit_menu(has_media: bool, has_video: bool) -> InlineKeyboardMarkup:
    rows = [[ib("🏷️ النشاط", "ds:back:biz"), ib("✍️ الرسالة", "ds:back:msg")]]
    second = [ib("🎨 الهوية", "ds:back:brand")]
    if has_media:
        second.insert(0, ib("📎 المواد", "ds:back:media"))
    rows.append(second)
    third = [ib("🗣️ اللغة والنبرة", "ds:back:lang"), ib("📝 الملاحظات", "ds:back:notes")]
    if has_video:
        third.insert(1, ib("🎬 الإضافات", "ds:back:extras"))
    rows.append(third)
    rows.append([ib("◀️ رجوع للملخص", "ds:back:summary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_done(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("📦 متابعة الطلب", f"ord:view:{order_id}", "primary")],
        [ib("🏠 القائمة", "nav:home")],
    ])


def ds_order_view(order: dict) -> InlineKeyboardMarkup:
    """بطاقة طلب التصميم عند العميل."""
    st, oid = order["status"], order["id"]
    rows = []
    if st == "delivered":
        rows.append([ib("✅ ممتاز — اعتمده", f"ds:approve:{oid}", "success")])
        rows.append([ib("✏️ طلب تعديل", f"ds:revise:{oid}")])
    if st in ("delivered", "completed") and order.get("delivery"):
        rows.append([ib("📥 أعد إرسال الملفات", f"ds:files:{oid}")])
    if st == "submitted":
        rows.append([ib("🚫 إلغاء واسترداد (مجاني قبل بدء العمل)", f"ds:cancel_order:{oid}", "danger")])
    if st in ("completed", "rejected", "cancelled", "refunded"):
        rows.append([ib("🎨 اطلب تصميماً جديداً", "nav:design", "primary")])
    if order.get("media_count"):
        rows.append([ib("🖼️ عرض ملفاتي", f"ord:media:{oid}")])
    rows.append([ib("💬 مساعدة بهذا الطلب", f"ord:help:{oid}")])
    rows.append([ib("◀️ طلباتي", "ord:list:1"), ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ds_delivered(order_id: int, free: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ ممتاز — اعتمده", f"ds:approve:{order_id}", "success")],
        [ib("✏️ طلب تعديل (مجاني × 1)" if free else "✏️ طلب تعديل (مدفوع)", f"ds:revise:{order_id}")],
        [ib("🆘 مشكلة بهذا الطلب", f"ord:help:{order_id}")],
    ])


def ds_cancel_confirm(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ نعم، ألغِ واسترد", f"ds:cancel_yes:{order_id}", "danger")],
        [ib("◀️ رجوع", f"ord:view:{order_id}")],
    ])


def ds_revision_confirm(order_id: int, fee: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"✅ تأكيد ودفع {fee}", f"ds:revise_pay:{order_id}", "success")],
        [ib("❌ إلغاء", f"ord:view:{order_id}", "danger")],
    ])


# ───────────── الأدمن — بطاقة مهمة التصميم + لوحة المهام ─────────────

def admin_ds_card(order: dict, media_count: int = 0, in_channel: bool = False) -> InlineKeyboardMarkup:
    oid, st = order["id"], order["status"]
    rows = []
    if st == "submitted":
        rows.append([ib("▶️ بدأت العمل", f"adm:ds:{oid}:start", "primary")])
    if st in ("submitted", "in_progress", "needs_revision"):
        rows.append([ib("📤 تسليم النسخة المعدّلة ✍️" if st == "needs_revision" else "📤 تسليم — أرسل الملف ✍️", f"adm:ds:{oid}:deliver", "success")])
    if st == "delivered":
        rows.append([ib("✅ اعتماد بالنيابة عن العميل", f"adm:ds:{oid}:approve")])
    if media_count:
        rows.append([ib(f"📎 ملفات العميل ({media_count})", f"adm:ord:{oid}:media")])
    if order.get("delivery"):
        rows.append([ib("📥 ما سلّمته", f"adm:ds:{oid}:files")])
    rows.append([ib("💬 مراسلة العميل", f"adm:ord:{oid}:msg")])
    if st in ("submitted", "in_progress", "needs_revision", "delivered"):
        rows.append([ib("❌ تعذّر التنفيذ — استرداد كامل", f"adm:ds:{oid}:reject", "danger")])
    if not in_channel:
        rows.append([ib("◀️ المهام", "adm:tasks")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_deliver_step(order_id: int, ready: bool) -> InlineKeyboardMarkup:
    rows = []
    if ready:
        rows.append([ib("✅ أرسل للعميل", f"adm:ds:{order_id}:send", "success")])
    rows.append([ib("❌ إلغاء", "adm:cancel_input", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tasks_list(rows_data: list[tuple[int, str, str | None]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for oid, label, style in rows_data:
        b.row(ib(label, f"adm:ord:{oid}:view", style))
    b.row(ib("🔄 تحديث", "adm:tasks"), ib("◀️ رجوع للوحة", "adm:panel"))
    return b.as_markup()

# ═══════════════════════════ v0.8.1 — 🎫 التذاكر + 📣 البث + 👤 المستخدم ═══════════════════════════

def ticket_order_choices(orders: list[dict]) -> InlineKeyboardMarkup:
    from app.services import orders as orders_svc
    rows = []
    for o in orders[:12]:
        label = f"{orders_svc.status_icon(o)} #ORD-{o['id']} · {T.esc((o.get('spec') or {}).get('title') or o.get('kind') or 'طلب')}"
        rows.append([ib(label[:60], f"tck:open:order:{o['id']}")])
    rows.append([ib("💰 الرصيد والشحن", "tck:open:topup")])
    rows.append([ib("❓ سؤال عام", "tck:open:general")])
    rows.append([ib("❌ إلغاء", "nav:home", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_list(items: list[dict], admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for t in items[:40]:
        icon = "🟡" if t.get("status") == "open" else ("🔵" if t.get("status") == "answered" else "✅")
        who = (t.get("user_name") or "")[:12] if admin else ""
        subject = f"#TCK-{t['id']}" + (f" · #ORD-{t['order_id']}" if t.get("order_id") else " · سؤال")
        label = f"{icon} {subject}" + (f" · {who}" if who else "")
        data = f"adm:tck:{t['id']}:view" if admin else f"tck:view:{t['id']}"
        rows.append([ib(label[:60], data)])
    rows.append([ib("🔄 تحديث", "adm:tickets" if admin else "sup:mine")])
    rows.append([ib("◀️ رجوع", "adm:panel" if admin else "sup:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_view(ticket: dict) -> InlineKeyboardMarkup:
    rows = []
    if ticket.get("status") != "closed":
        rows.append([ib("↩️ إضافة رسالة", f"tck:reply:{ticket['id']}", "primary")])
        rows.append([ib("✅ إغلاق التذكرة", f"tck:close:{ticket['id']}", "success")])
    rows.append([ib("📂 تذاكري", "sup:mine"), ib("🏠 القائمة", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ticket_card(ticket: dict) -> InlineKeyboardMarkup:
    tid = ticket["id"]
    rows = []
    if ticket.get("status") != "closed":
        rows.append([ib("↩️ رد للعميل", f"adm:tck:{tid}:reply", "primary")])
        rows.append([ib("✅ إغلاق التذكرة", f"adm:tck:{tid}:close", "success")])
    if ticket.get("order_id"):
        rows.append([ib(f"📦 فتح #ORD-{ticket['order_id']}", f"adm:ord:{ticket['order_id']}:view")])
    rows.append([ib("🎫 التذاكر", "adm:tickets"), ib("🛠️ اللوحة", "adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_photo() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("⏭️ بدون صورة", "adm:bc:no_photo", "primary")],
        [ib("❌ إلغاء", "adm:bc:cancel", "danger")],
    ])


def broadcast_audience(counts: dict[str, int]) -> InlineKeyboardMarkup:
    labels = [
        ("all", f"👥 الجميع · {counts.get('all', 0)}"),
        ("balance", f"💰 لديهم رصيد · {counts.get('balance', 0)}"),
        ("ordered", f"📦 طلبوا سابقاً · {counts.get('ordered', 0)}"),
        ("new", f"🆕 لم يطلبوا بعد · {counts.get('new', 0)}"),
    ]
    rows = [[ib(label, f"adm:bc:aud:{code}")] for code, label in labels]
    rows.append([ib("❌ إلغاء", "adm:bc:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_confirm(count: int, big: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if big:
        rows.append([ib(f"⚠️ تأكيد البث لـ {count}", "adm:bc:confirm2", "danger")])
    else:
        rows.append([ib(f"📣 ابدأ البث لـ {count}", "adm:bc:confirm", "success")])
    rows.append([ib("✏️ تعديل المحتوى", "adm:bc:edit")])
    rows.append([ib("❌ إلغاء", "adm:bc:cancel", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_card(user: dict) -> InlineKeyboardMarkup:
    uid = int(user["tg_id"])
    blocked = bool(user.get("is_blocked"))
    rows = [[ib("➕ إضافة رصيد", f"adm:user:{uid}:add", "success"),
             ib("➖ خصم رصيد", f"adm:user:{uid}:sub", "danger")],
            [ib("📦 طلباته", f"adm:user:{uid}:orders"), ib("🎫 تذاكره", f"adm:user:{uid}:tickets")],
            [ib(("🔓 فك الحظر" if blocked else "🚫 حظر"), f"adm:user:{uid}:toggle", "danger" if not blocked else "success")],
            [ib("👤 بحث جديد", "adm:find"), ib("◀️ اللوحة", "adm:panel")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_balance_confirm(uid: int, verb: str, amount: str, nonce: str = "") -> InlineKeyboardMarkup:
    # nonce (v0.9.2): رمز لمرة واحدة — الضغطة المكررة لا تضيف/تخصم مرتين
    data = f"adm:bal:confirm:{uid}:{nonce}" if nonce else f"adm:bal:confirm:{uid}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"✅ تأكيد {verb} {amount}", data, "success")],
        [ib("✏️ تعديل المبلغ/السبب", f"adm:user:{uid}:{'add' if verb == 'إضافة' else 'sub'}")],
        [ib("❌ إلغاء", "adm:cancel_input", "danger")],
    ])


def admin_topup_adjust_confirm(tid: int, amount: str) -> InlineKeyboardMarkup:
    """تأكيد ثانٍ عندما يختلف المبلغ المعدَّل كثيراً عن طلب العميل (v0.9.2)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(f"✅ نعم، اعتمد {amount}", f"adm:top:{tid}:adjc", "success")],
        [ib("✏️ مبلغ آخر", f"adm:top:{tid}:adj"), ib("❌ إلغاء", "adm:cancel_input", "danger")],
    ])


def admin_wallet_confirm(code: str) -> InlineKeyboardMarkup:
    """مراجعة عنوان المحفظة قبل الحفظ (v0.9.2)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ صحيح — احفظه", f"adm:wal:{code}:save", "success")],
        [ib("✏️ إعادة الإدخال", f"adm:wal:{code}:edit"), ib("❌ إلغاء", f"adm:wal:{code}", "danger")],
    ])


def admin_rate_confirm() -> InlineKeyboardMarkup:
    """تأكيد تغيير كبير في سعر صرف الليرة (v0.9.2)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib("✅ نعم، السعر صحيح", "adm:rate:save", "success")],
        [ib("✏️ إدخال آخر", "adm:rate"), ib("❌ إلغاء", "adm:wallets", "danger")],
    ])
