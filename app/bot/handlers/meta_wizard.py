"""M0–M8 — معالج طلب إعلان فيسبوك/إنستغرام (خريطة الأزرار، الفرع M).

المسار: الباقة ← (مخصص: يومي + أيام) ← المنصة ← الهدف ← الدولة ← (سوريا: المحافظات) ← الجمهور ←
الرابط ← الوصف ← الملفات ← (نص إعلاني؟) ← الواتساب ← (معرّف تيليغرام؟) ← الملخص ← تأكيد ← #ORD

قواعد:
- لا خصم قبل شاشة الملخص وزر «تأكيد» المكتوب عليه المبلغ.
- كل اختيارات العميل في FSM data (تنجو من إعادة تشغيل Render لأن التخزين في Postgres).
- رصيد ناقص → مسودة awaiting_payment + اقتراح شحن الفرق + «أكمل طلبي المعلّق».
- كل شاشة تُحرَّر في الرسالة نفسها (شاشة واحدة تتبدّل)؛ الإدخالات النصية تُرسل شاشة جديدة.
"""

from __future__ import annotations

import html
import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import events, orders as orders_repo, settings as settings_repo, users as users_repo
from app.services import nour, orders as orders_svc, pricing as P, targeting as TG, validators as V
from app.services.money import InsufficientBalance
from app.services.pricing import fmt, money

router = Router(name="meta_wizard")
log = logging.getLogger("meta")

MAX_MEDIA = 3
DESC_MIN, DESC_MAX = 10, 600


class Meta(StatesGroup):
    daily = State()      # مخصص: ينتظر رقم الميزانية اليومية
    days = State()       # مخصص: ينتظر عدد الأيام
    choosing = State()   # شاشات الأزرار (منصة/هدف/دولة/جمهور/ملخص)
    link = State()
    desc = State()
    media = State()
    whatsapp = State()


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def _media_word(n: int) -> str:
    n = int(n or 0)
    return "لا شيء" if n == 0 else ("ملف واحد" if n == 1 else ("ملفان" if n == 2 else f"{n} ملفات"))


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — رسالة بصورة أو لم تتغير
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass


async def _send(target, text: str, kb, new_message: bool) -> None:
    """target = CallbackQuery أو Message. new_message=True يرسل رسالة جديدة دائماً."""
    if isinstance(target, CallbackQuery):
        if new_message:
            await target.message.answer(text, reply_markup=kb)
        else:
            await _edit(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb)


def _pkg_label(d: dict) -> str:
    code = d.get("pkg")
    if code in P.META_BY_CODE:
        p = P.META_BY_CODE[code]
        return f"{p.emoji} {p.title}"
    if code == "bundle":
        return f"📦 {P.BUNDLE_STORE_LAUNCH['title']}"
    return "🛠️ مخصص"


def _skip_text(message: Message) -> bool:
    return bool(message.text) and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS)


# ───────────── M0 الباقة ─────────────

async def _start_pkg(cb: CallbackQuery, state: FSMContext, code: str) -> None:
    await state.clear()
    data = {"pkg": code, "gender": "all", "age_min": 18, "age_max": 65, "media": [], "addons": []}
    if code in P.META_BY_CODE:
        p = P.META_BY_CODE[code]
        data.update(daily=str(p.daily), days=p.days)
    elif code == "bundle":
        p = P.META_BY_CODE[P.BUNDLE_STORE_LAUNCH["package"]]
        data.update(daily=str(p.daily), days=p.days, addons=list(P.BUNDLE_STORE_LAUNCH["addons"]), bundle=True)
    await state.update_data(**data)
    await events.log_event("meta_start", cb.from_user.id, pkg=code)
    if code == "custom":
        await state.set_state(Meta.daily)
        await _edit(cb, T.META_CUSTOM_DAILY, K.meta_custom_daily())
        return
    await _show_platform(cb, state)


@router.callback_query(F.data.startswith("meta:pkg:"))
async def cb_pkg(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    svc = await settings_repo.services()
    if not svc["meta"]:
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    if code not in P.META_BY_CODE and code not in ("custom", "bundle"):
        await cb.answer()
        return
    await _start_pkg(cb, state, code)
    await cb.answer()


# ───────────── مخصص: الميزانية اليومية والأيام ─────────────

async def _set_daily(target, state: FSMContext, raw: str, edit: bool) -> None:
    val = V.parse_decimal_str(raw)
    if val is None or not (P.META_MIN_DAILY <= Decimal(val) <= P.META_MAX_DAILY):
        if edit:
            await target.answer(T.META_CUSTOM_INVALID_DAILY.replace("<code>", "").replace("</code>", ""), show_alert=True)
        else:
            await target.answer(T.META_CUSTOM_INVALID_DAILY)
        return
    await state.update_data(daily=str(money(val)))
    await state.set_state(Meta.days)
    text = T.META_CUSTOM_DAYS.format(daily=fmt(val))
    if edit:
        await _edit(target, text, K.meta_custom_days())
    else:
        await target.answer(text, reply_markup=K.meta_custom_days())


@router.callback_query(Meta.daily, F.data.startswith("meta:daily:"))
async def cb_daily(cb: CallbackQuery, state: FSMContext) -> None:
    await _set_daily(cb, state, cb.data.split(":")[2], edit=True)
    await cb.answer()


@router.message(Meta.daily, F.text)
async def msg_daily(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    await _set_daily(message, state, message.text, edit=False)


async def _set_days(target, state: FSMContext, raw: str, edit: bool) -> None:
    n = V.parse_int(raw)
    if n is None or not (P.META_MIN_DAYS <= n <= P.META_MAX_DAYS):
        if edit:
            await target.answer(T.META_CUSTOM_INVALID_DAYS.replace("<code>", "").replace("</code>", ""), show_alert=True)
        else:
            await target.answer(T.META_CUSTOM_INVALID_DAYS)
        return
    await state.update_data(days=n)
    if edit:
        await _show_platform(target, state)
    else:
        await _show_platform(target, state, new_message=True)


@router.callback_query(Meta.days, F.data.startswith("meta:days:"))
async def cb_days(cb: CallbackQuery, state: FSMContext) -> None:
    await _set_days(cb, state, cb.data.split(":")[2], edit=True)
    await cb.answer()


@router.message(Meta.days, F.text)
async def msg_days(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    await _set_days(message, state, message.text, edit=False)


# ───────────── M1 المنصة ─────────────

async def _show_platform(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Meta.choosing)
    daily = Decimal(d["daily"])
    both = daily >= P.META_BOTH_MIN_DAILY
    budget, price, _ = P.meta_custom_price(daily, int(d["days"]))
    pkg_line = f"{_pkg_label(d)} — {fmt(daily)} × {P.days_word(d['days'])} = ميزانية {fmt(budget)} → السعر <b>{fmt(price)}</b>"
    text = T.META_PLATFORM.format(pkg=pkg_line, both_note=T.META_PLATFORM_BOTH_OK if both else T.META_PLATFORM_BOTH_NO)
    back = "meta:pkg:custom" if d.get("pkg") == "custom" else "meta:pkgs"
    await _send(target, text, K.meta_platform(both, back), new_message)


@router.callback_query(Meta.choosing, F.data.startswith("meta:plat:"))
async def cb_platform(cb: CallbackQuery, state: FSMContext) -> None:
    plat = cb.data.split(":")[2]
    d = await state.get_data()
    if plat == "both" and Decimal(d["daily"]) < P.META_BOTH_MIN_DAILY:
        await cb.answer("«كلاهما» يحتاج 4$ يومياً على الأقل", show_alert=True)
        return
    if plat not in TG.PLATFORM_NAME:
        await cb.answer()
        return
    await state.update_data(platform=plat)
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_goal(cb)
    await cb.answer()


# ───────────── M2 الهدف ─────────────

async def _show_goal(cb: CallbackQuery) -> None:
    goals = "\n".join(f"• <b>{name}</b> — {hint}" for _, name, hint in TG.GOALS)
    await _edit(cb, T.META_GOAL.format(goals=goals), K.meta_goal())


@router.callback_query(Meta.choosing, F.data.startswith("meta:goal:"))
async def cb_goal(cb: CallbackQuery, state: FSMContext) -> None:
    goal = cb.data.split(":")[2]
    if goal not in TG.GOAL_NAME:
        await cb.answer()
        return
    await state.update_data(goal=goal)
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _edit(cb, T.META_COUNTRY, K.meta_country())
    await cb.answer()


# ───────────── M3 الدولة والمحافظات ─────────────

@router.callback_query(Meta.choosing, F.data.startswith("meta:ctry_page:"))
async def cb_country_page(cb: CallbackQuery) -> None:
    more = cb.data.endswith(":1")
    await _edit(cb, T.META_COUNTRY, K.meta_country(more))
    await cb.answer()


@router.callback_query(Meta.choosing, F.data.startswith("meta:ctry:"))
async def cb_country(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    if code not in TG.COUNTRY_BY_CODE:
        await cb.answer()
        return
    await state.update_data(country=code, provinces=[])
    if code in TG.PROVINCES:
        await _show_provinces(cb, state)
    else:
        d = await state.get_data()
        if d.get("editing"):
            await _show_summary(cb, state)
        else:
            await _show_audience(cb, state)
    await cb.answer()


async def _show_provinces(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    sel = d.get("provinces") or []
    text = T.META_PROVINCES.format(country=TG.country_label(d["country"]),
                                   selected=TG.provinces_label(d["country"], sel) if sel else "كل الدولة")
    await _edit(cb, text, K.meta_provinces(d["country"], sel))


@router.callback_query(Meta.choosing, F.data.startswith("meta:prov:"))
async def cb_province(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":")[2]
    d = await state.get_data()
    sel = list(d.get("provinces") or [])
    if key in sel:
        sel.remove(key)
    elif key in TG.PROVINCE_NAME:
        sel.append(key)
    await state.update_data(provinces=sel)
    await _show_provinces(cb, state)
    await cb.answer()


@router.callback_query(Meta.choosing, F.data == "meta:prov_clear")
async def cb_province_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(provinces=[])
    await _show_provinces(cb, state)
    await cb.answer("تم المسح")


@router.callback_query(Meta.choosing, F.data == "meta:prov_done")
async def cb_province_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_audience(cb, state)
    await cb.answer()


# ───────────── M4 الجمهور ─────────────

async def _show_audience(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    text = T.META_AUDIENCE.format(gender=TG.GENDER_NAME.get(d.get("gender", "all")),
                                  age=TG.age_label(int(d.get("age_min", 18)), int(d.get("age_max", 65))))
    await _edit(cb, text, K.meta_audience(d.get("gender", "all"), int(d.get("age_min", 18)), int(d.get("age_max", 65))))


@router.callback_query(Meta.choosing, F.data.startswith("meta:gender:"))
async def cb_gender(cb: CallbackQuery, state: FSMContext) -> None:
    g = cb.data.split(":")[2]
    if g in TG.GENDER_NAME:
        await state.update_data(gender=g)
    await _show_audience(cb, state)
    await cb.answer()


@router.callback_query(Meta.choosing, F.data.startswith("meta:age:"))
async def cb_age(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, lo, hi = cb.data.split(":")
    lo, hi = int(lo), int(hi)
    if 13 <= lo < hi <= 65:
        await state.update_data(age_min=lo, age_max=hi)
    await _show_audience(cb, state)
    await cb.answer()


@router.callback_query(Meta.choosing, F.data == "meta:aud_done")
async def cb_audience_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await state.set_state(Meta.link)
        await _edit(cb, T.META_CONTENT_LINK, K.meta_text_step("meta:back:audience"))
    await cb.answer()


# ───────────── M5 المحتوى: رابط ← وصف ← ملفات ← نص إعلاني ─────────────

@router.message(Meta.link, F.text)
async def msg_link(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    url = V.clean_url(message.text)
    if not url:
        await message.answer(T.META_CONTENT_LINK_INVALID)
        return
    await state.update_data(link=url)
    d = await state.get_data()
    if d.get("editing"):
        await state.set_state(Meta.choosing)
        await _show_summary(message, state, new_message=True)
        return
    await state.set_state(Meta.desc)
    await message.answer(T.META_CONTENT_DESC, reply_markup=K.meta_text_step("meta:back:link"))


@router.message(Meta.desc, F.text)
async def msg_desc(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    desc = " ".join(message.text.split())
    if not (DESC_MIN <= len(desc) <= DESC_MAX):
        await message.answer(T.META_CONTENT_DESC_INVALID)
        return
    await state.update_data(desc=desc)
    d = await state.get_data()
    if d.get("editing"):
        await state.set_state(Meta.choosing)
        await _show_summary(message, state, new_message=True)
        return
    await state.set_state(Meta.media)
    await message.answer(T.META_CONTENT_MEDIA.format(n=len(d.get("media") or [])), reply_markup=K.meta_media(len(d.get("media") or [])))


@router.message(Meta.media, F.photo | F.video | F.document)
async def msg_media(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    items = list(d.get("media") or [])
    if len(items) >= MAX_MEDIA:
        await message.answer(f"وصلنا للحد الأقصى ({MAX_MEDIA} ملفات) — اضغط «تم» للمتابعة.", reply_markup=K.meta_media(len(items)))
        return
    if message.photo:
        items.append(["photo", message.photo[-1].file_id])
    elif message.video:
        items.append(["video", message.video.file_id])
    elif message.document:
        mt = message.document.mime_type or ""
        if not (mt.startswith("image/") or mt.startswith("video/")):
            await message.answer("أرسل صورة أو فيديو فقط 🙂")
            return
        items.append(["document", message.document.file_id])
    await state.update_data(media=items)
    await message.answer(T.META_CONTENT_MEDIA.format(n=len(items)), reply_markup=K.meta_media(len(items)))


@router.message(Meta.media, F.text)
async def msg_media_text(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    d = await state.get_data()
    await message.answer("أرسل صورة/فيديو، أو اضغط «تخطّي» 👇", reply_markup=K.meta_media(len(d.get("media") or [])))


@router.callback_query(Meta.media, F.data == "meta:media_clear")
async def cb_media_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(media=[])
    await _edit(cb, T.META_CONTENT_MEDIA.format(n=0), K.meta_media(0))
    await cb.answer("تم الحذف")


@router.callback_query(Meta.media, F.data == "meta:media_done")
async def cb_media_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await state.set_state(Meta.choosing)
    if d.get("editing") or d.get("bundle"):
        # الباقة تتضمن النص أصلاً — لا نسأل
        if d.get("editing"):
            await _show_summary(cb, state)
        else:
            await _ask_whatsapp(cb, state)
    else:
        await _edit(cb, T.META_ADDON_COPY.format(price=fmt(P.ADDONS["copy"]["price"])), K.meta_addon_copy(fmt(P.ADDONS["copy"]["price"])))
    await cb.answer()


@router.callback_query(Meta.choosing, F.data.startswith("meta:addon:copy:"))
async def cb_addon_copy(cb: CallbackQuery, state: FSMContext) -> None:
    want = cb.data.endswith(":1")
    d = await state.get_data()
    addons = [a for a in (d.get("addons") or []) if a != "copy"]
    if want:
        addons.append("copy")
    await state.update_data(addons=addons)
    await _ask_whatsapp(cb, state)
    await cb.answer()


# ───────────── M6 الواتساب + المعرّف ─────────────

async def _ask_whatsapp(target, state: FSMContext, new_message: bool = False) -> None:
    await state.set_state(Meta.whatsapp)
    await _send(target, T.META_WHATSAPP, K.meta_text_step("meta:back:media"), new_message)


@router.message(Meta.whatsapp, F.text | F.contact)
async def msg_whatsapp(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        return
    raw = message.contact.phone_number if message.contact else message.text
    wa = V.clean_whatsapp(raw)
    if not wa:
        await message.answer(T.META_WHATSAPP_INVALID)
        return
    await state.update_data(whatsapp=wa)
    await state.set_state(Meta.choosing)
    await message.answer(T.META_WHATSAPP_CONFIRM.format(wa=wa), reply_markup=K.meta_whatsapp_confirm())


@router.callback_query(Meta.choosing, F.data == "meta:wa_ok")
async def cb_wa_ok(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    uname = V.clean_username(cb.from_user.username)
    if uname:
        await state.update_data(tg_username=uname)
        await _show_summary(cb, state)
    elif d.get("uname_skipped") or d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _edit(cb, T.META_USERNAME_MISSING, K.meta_username_missing())
    await cb.answer()


@router.callback_query(Meta.choosing, F.data == "meta:uname_check")
async def cb_uname_check(cb: CallbackQuery, state: FSMContext) -> None:
    uname = V.clean_username(cb.from_user.username)
    if not uname:
        await cb.answer(T.META_USERNAME_STILL_MISSING, show_alert=True)
        return
    await users_repo.upsert_user(cb.from_user.id, cb.from_user.full_name or "", cb.from_user.username)
    await state.update_data(tg_username=uname)
    await cb.answer(T.META_USERNAME_OK.format(username=uname))
    await _show_summary(cb, state)


@router.callback_query(Meta.choosing, F.data == "meta:uname_skip")
async def cb_uname_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(tg_username=None, uname_skipped=True)
    await _show_summary(cb, state)
    await cb.answer()


# ───────────── M7 الملخص ─────────────

def _spec_from_state(d: dict) -> dict:
    spec = {
        "pkg": d.get("pkg"), "daily": d["daily"], "days": int(d["days"]), "platform": d["platform"],
        "goal": d.get("goal", "post_promotion"), "country": d["country"], "provinces": d.get("provinces") or [],
        "gender": d.get("gender", "all"), "age_min": int(d.get("age_min", 18)), "age_max": int(d.get("age_max", 65)),
        "link": d.get("link"), "desc": d.get("desc"), "media_count": len(d.get("media") or []),
        "addons": d.get("addons") or [], "bundle": bool(d.get("bundle")),
        "whatsapp": d.get("whatsapp"), "tg_username": d.get("tg_username"),
    }
    if spec["bundle"]:
        spec["price_override"] = str(P.BUNDLE_STORE_LAUNCH["price"])
    return spec


def summary_text(spec: dict, price: Decimal, balance: Decimal) -> tuple[str, bool, Decimal]:
    budget = money(Decimal(spec["daily"]) * int(spec["days"]))
    addons = spec.get("addons") or []
    addon_line = ""
    if spec.get("bundle"):
        addon_line = T.META_ADDON_LINE.format(names="نص إعلاني + تصميم صورة (ضمن الباقة)", total="0$")
    elif addons:
        addon_line = T.META_ADDON_LINE.format(names=" + ".join(P.ADDONS[a]["title"] for a in addons),
                                              total=fmt(sum(P.ADDONS[a]["price"] for a in addons)))
    ok = balance >= price
    gap = money(price - balance) if not ok else Decimal("0")
    text = T.META_SUMMARY.format(
        pkg=_pkg_label(spec), platform=TG.PLATFORM_NAME[spec["platform"]], goal=TG.GOAL_NAME.get(spec["goal"], spec["goal"]),
        geo=f"{TG.country_label(spec['country'])} — {TG.provinces_label(spec['country'], spec.get('provinces'))}",
        gender=TG.GENDER_NAME.get(spec["gender"], spec["gender"]), age=TG.age_label(spec["age_min"], spec["age_max"]),
        daily=fmt(spec["daily"]), days=P.days_word(spec["days"]), budget=fmt(budget), link=esc(spec.get("link")) or "—",
        desc=esc(spec.get("desc")) or "—", media=_media_word(spec.get("media_count", 0)),
        addons=addon_line, wa=spec.get("whatsapp") or "—",
        tg=f"@{spec['tg_username']}" if spec.get("tg_username") else "بلا معرّف (واتساب فقط)",
        price=fmt(price), balance=fmt(balance),
        balance_line=T.META_SUMMARY_OK if ok else T.META_SUMMARY_GAP.format(gap=fmt(gap)),
    )
    return text, ok, gap


async def _show_summary(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Meta.choosing)
    await state.update_data(editing=False)
    spec = _spec_from_state(d)
    _, price, cost = orders_svc.compute_prices(spec)
    uid = target.from_user.id
    balance = await users_repo.get_balance(uid)
    text, ok, gap = summary_text(spec, price, balance)
    kb = K.meta_summary(ok, fmt(price), fmt(gap) if not ok else None)
    if not ok:
        # نحفظ مسودة فوراً حتى لا يضيع شيء لو خرج للشحن
        days_valid = int(await settings_repo.get("order_draft_days", 7))
        draft = await orders_repo.save_awaiting(uid, spec, price, cost, days_valid, d.get("draft_id"))
        await state.update_data(draft_id=draft["id"], gap_usd=str(gap))
        if isinstance(target, CallbackQuery) and not new_message:
            await orders_repo.set_messages(draft["id"], user_msg_id=target.message.message_id)
    await _send(target, text, kb, new_message)


@router.callback_query(Meta.choosing, F.data == "meta:edit")
async def cb_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(editing=True)
    await _edit(cb, "✏️ <b>شو بدك تعدّل؟</b>", K.meta_edit_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("meta:back:"))
async def cb_back(cb: CallbackQuery, state: FSMContext) -> None:
    """رجوع/تعديل إلى شاشة معيّنة — يعمل من أي حالة داخل المعالج."""
    where = cb.data.split(":")[2]
    d = await state.get_data()
    if not d.get("daily"):
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    await state.set_state(Meta.choosing)
    if where == "platform":
        await _show_platform(cb, state)
    elif where == "goal":
        await _show_goal(cb)
    elif where == "country":
        await _edit(cb, T.META_COUNTRY, K.meta_country())
    elif where == "audience":
        await _show_audience(cb, state)
    elif where == "link":
        await state.set_state(Meta.link)
        await _edit(cb, T.META_CONTENT_LINK, K.meta_text_step("meta:back:audience"))
    elif where == "desc":
        await state.set_state(Meta.desc)
        await _edit(cb, T.META_CONTENT_DESC, K.meta_text_step("meta:back:link"))
    elif where == "media":
        await state.set_state(Meta.media)
        n = len(d.get("media") or [])
        await _edit(cb, T.META_CONTENT_MEDIA.format(n=n), K.meta_media(n))
    elif where == "whatsapp":
        await _ask_whatsapp(cb, state)
    elif where == "summary":
        await _show_summary(cb, state)
    await cb.answer()


# ───────────── M8 التأكيد ─────────────

@router.callback_query(Meta.choosing, F.data == "meta:confirm")
async def cb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("whatsapp") or not d.get("platform"):
        await cb.answer("الطلب ناقص — راجع الملخص", show_alert=True)
        return
    spec = _spec_from_state(d)
    # منع الضغط المزدوج: نعطّل الأزرار فوراً
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    try:
        order = await orders_svc.confirm(cb.from_user.id, spec, d.get("draft_id"))
    except InsufficientBalance:
        await cb.answer(T.META_INSUFFICIENT_RACE, show_alert=True)
        await _show_summary(cb, state)
        return
    if d.get("media"):
        await orders_repo.add_media(order["id"], [(k, f) for k, f in d["media"]])
    await state.clear()
    await cb.answer("✅ تم الخصم — جاري الإرسال")
    balance = await users_repo.get_balance(cb.from_user.id)
    text = T.META_DONE.format(id=order["id"], price=fmt(order["price_usd"]), balance=fmt(balance), wa=spec["whatsapp"])
    if nour.is_dry_run():
        text += T.META_DONE_DRY
    sent = None
    try:
        await cb.message.edit_text(text, reply_markup=K.meta_done(order["id"]))
        sent = cb.message
    except Exception:  # noqa: BLE001
        sent = await cb.message.answer(text, reply_markup=K.meta_done(order["id"]))
    if sent:
        await orders_repo.set_messages(order["id"], user_msg_id=sent.message_id)
    # الإرسال إلى نور (أو المحاكاة) + بطاقة الأدمن
    from app.services import order_notify
    await orders_svc.submit(order["id"])
    await order_notify.notify_admins_new_order(cb.bot, order["id"])


# ───────────── الرصيد الناقص: شحن الفرق / استئناف المسودة ─────────────

@router.callback_query(F.data == "meta:topup_gap")
async def cb_topup_gap(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    gap = d.get("gap_usd")
    if not gap:
        draft = await orders_repo.get_awaiting(cb.from_user.id)
        if draft:
            bal = await users_repo.get_balance(cb.from_user.id)
            gap = str(max(Decimal("0"), Decimal(draft["price_usd"]) - bal))
    await state.clear()
    if gap:
        await state.update_data(gap_usd=gap)
    from app.bot.handlers.topup import cb_topup  # نفس شاشة اختيار طريقة الدفع
    await cb_topup(cb, state, keep_data=True)


@router.callback_query(F.data == "ord:resume")
async def cb_resume(cb: CallbackQuery, state: FSMContext) -> None:
    draft = await orders_repo.get_awaiting(cb.from_user.id)
    if not draft:
        await cb.answer(T.META_NO_DRAFT, show_alert=True)
        return
    spec = draft["spec"]
    if draft.get("kind") == "tg_ads":
        from app.bot.handlers.tg_ads_wizard import resume_draft
        await resume_draft(cb, state, draft)
        await cb.answer()
        return
    await state.clear()
    await state.update_data(
        pkg=spec.get("pkg"), daily=spec["daily"], days=int(spec["days"]), platform=spec["platform"], goal=spec.get("goal"),
        country=spec["country"], provinces=spec.get("provinces") or [], gender=spec.get("gender", "all"),
        age_min=spec.get("age_min", 18), age_max=spec.get("age_max", 65), link=spec.get("link"), desc=spec.get("desc"),
        media=[], addons=spec.get("addons") or [], bundle=spec.get("bundle", False), whatsapp=spec.get("whatsapp"),
        tg_username=spec.get("tg_username") or V.clean_username(cb.from_user.username), uname_skipped=True,
        draft_id=draft["id"],
    )
    try:
        await cb.message.answer(T.META_DRAFT_RESUME.format(id=draft["id"]))
    except Exception:  # noqa: BLE001
        pass
    await _show_summary(cb, state, new_message=True)
    await cb.answer()


@router.callback_query(F.data.startswith("ord:draft_cancel:"))
async def cb_draft_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    ok = await orders_repo.cancel_awaiting(oid, cb.from_user.id)
    await state.clear()
    if ok:
        await events.log_event("order_draft_cancelled", cb.from_user.id, oid)
        await _edit(cb, T.META_DRAFT_CANCELLED.format(id=oid), K.home_only())
        await cb.answer("تم الإلغاء")
    else:
        await cb.answer("هذه المسودة لم تعد موجودة", show_alert=True)


@router.callback_query(F.data == "meta:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await state.clear()
    if d.get("draft_id"):
        await orders_repo.cancel_awaiting(d["draft_id"], cb.from_user.id)
    await events.log_event("meta_cancel", cb.from_user.id)
    await _edit(cb, T.META_CANCEL_WIZARD, K.home_only())
    await cb.answer()


@router.callback_query(F.data == "meta:pkgs")
async def cb_pkgs(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    svc = await settings_repo.services()
    draft = await orders_repo.get_awaiting(cb.from_user.id)
    await _edit(cb, T.meta_intro_v3(), K.meta_packages(enabled=svc["meta"], has_draft=bool(draft)))
    await cb.answer()
