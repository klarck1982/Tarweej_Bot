"""📣 Telegram Ads — معالج الطلب TA1–TA6 (تنفيذ يدوي من حساب الأدمن الإعلاني).

TA1 الميزانية (10–500$) ← TA2 الاستهداف (قنوات / اهتمامات / دولة+لغة / خبرة) ← TA3 النص (≤160) أو «اكتبولي +5$»
← TA4 الرابط (داخل تيليغرام فقط) ← TA5 الملخص والتأكيد (الخصم هنا فقط) ← TA6 تم.
رصيد ناقص → مسودة awaiting_payment + «اشحن الفرق» (نفس آلية Meta؛ الاستئناف من ord:resume يعرف النوع).
"""

from __future__ import annotations

import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import checkout as CO
from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import events, orders as orders_repo, settings as settings_repo, users as users_repo
from app.services import orders as orders_svc, pricing as P, targeting as TG, validators as V
from app.services import cpanel as CP
from app.services.money import InsufficientBalance
from app.services.pricing import fmt, money

router = Router(name="tg_ads_wizard")


class TgAds(StatesGroup):
    budget = State()
    channels = State()
    text = State()
    link = State()
    choosing = State()      # أي شاشة أزرار (الاستهداف، الملخص…)


_TME_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})(?:/(\d+))?/?$", re.I)
_AT_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def clean_tme(raw: str) -> str | None:
    """يقبل @name · t.me/name · t.me/name/123 · https://t.me/name — ويعيد t.me/... موحّداً."""
    s = (raw or "").strip()
    m = _TME_RE.match(s)
    if m:
        return f"t.me/{m.group(1)}" + (f"/{m.group(2)}" if m.group(2) else "")
    m = _AT_RE.match(s)
    if m:
        return f"t.me/{m.group(1)}"
    return None


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass


async def _send(target, text: str, kb, new_message: bool) -> None:
    if isinstance(target, CallbackQuery):
        if new_message:
            await target.message.answer(text, reply_markup=kb)
        else:
            await _edit(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb)


def _skip_text(message: Message) -> bool:
    return bool(message.text) and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS)


async def _hours() -> str:
    return str(await settings_repo.get("tg_ads_review_hours", "1 – 24"))


# ───────────── TA1 الميزانية ─────────────

@router.callback_query(F.data == "tga:start")
async def cb_start(cb: CallbackQuery, state: FSMContext) -> None:
    svc = await settings_repo.services()
    if not svc.get("tg_ads", True):
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    await state.clear()
    await state.set_state(TgAds.budget)
    await state.update_data(kind="tg_ads")
    await events.log_event("tga_start", cb.from_user.id)
    await _edit(cb, T.tga_intro_note(await _hours()), K.tga_budget())
    await cb.answer()


async def _set_budget(target, state: FSMContext, raw: str, edit: bool) -> None:
    val = V.parse_decimal_str(raw)
    ok = val is not None and P.TG_ADS_MIN_BUDGET <= Decimal(val) <= P.TG_ADS_MAX_BUDGET
    if not ok:
        if edit:
            await target.answer(T.tga_budget_invalid().replace("<code>", "").replace("</code>", ""), show_alert=True)
        else:
            await target.answer(T.tga_budget_invalid())
        return
    await state.update_data(budget=str(money(Decimal(val))))
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(target, state)
        return
    await _show_mode(target, state, new_message=not edit)


@router.callback_query(TgAds.budget, F.data == "tga:budget:type")
async def cb_budget_type(cb: CallbackQuery) -> None:
    await cb.message.answer(T.TGA_BUDGET_TYPE.format(min=fmt(P.TG_ADS_MIN_BUDGET), max=fmt(P.TG_ADS_MAX_BUDGET)),
                            reply_markup=K.cancel_input("tga:cancel"))
    await cb.answer()


@router.callback_query(TgAds.budget, F.data.startswith("tga:budget:"))
async def cb_budget(cb: CallbackQuery, state: FSMContext) -> None:
    await _set_budget(cb, state, cb.data.split(":")[2], edit=True)
    await cb.answer()


@router.message(TgAds.budget, F.text)
async def msg_budget(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    await _set_budget(message, state, message.text, edit=False)


# ───────────── TA2 الاستهداف ─────────────

async def _show_mode(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(TgAds.choosing)
    _, price, _ = P.tg_ads_quote(d["budget"], copy_addon="copy" in (d.get("addons") or []))
    await _send(target, T.TGA_TARGET.format(budget=fmt(d["budget"]), price=fmt(price)), K.tga_target_mode(), new_message)


@router.callback_query(TgAds.choosing, F.data.startswith("tga:mode:"))
async def cb_mode(cb: CallbackQuery, state: FSMContext) -> None:
    mode = cb.data.split(":")[2]
    if mode not in TG.TGA_MODES:
        await cb.answer()
        return
    await state.update_data(target_mode=mode)
    if mode == "channels":
        await state.set_state(TgAds.channels)
        await _edit(cb, T.TGA_CHANNELS.format(max=TG.TGA_MAX_CHANNELS), K.tga_text_step("tga:back:mode"))
    elif mode == "interests":
        d = await state.get_data()
        sel = d.get("interests") or []
        await _edit(cb, T.TGA_INTERESTS.format(max=TG.TGA_MAX_INTERESTS, selected=_interests_label(sel)), K.tga_interests(sel))
    elif mode == "geo":
        await _edit(cb, T.TGA_GEO, K.tga_country())
    else:
        await state.update_data(channels=[], interests=[])
        await _after_targeting(cb, state)
    await cb.answer()


def _interests_label(sel: list[str]) -> str:
    return "، ".join(TG.TGA_INTEREST_NAME.get(i, i) for i in sel) if sel else "لا شيء بعد"


@router.message(TgAds.channels, F.text)
async def msg_channels(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    found = []
    for tok in re.split(r"[\s,،]+", message.text):
        c = clean_tme(tok)
        if c and "/" not in c[5:] and c not in found:  # قنوات فقط (بلا رقم منشور)
            found.append(c)
    if not found:
        await message.answer(T.TGA_CHANNELS_INVALID)
        return
    found = found[: TG.TGA_MAX_CHANNELS]
    await state.update_data(channels=found)
    await message.answer(f"📡 قنوات مستهدفة: <b>{len(found)}</b> — " + "، ".join(found))
    await _after_targeting(message, state, new_message=True)


@router.callback_query(TgAds.choosing, F.data.startswith("tga:int:"))
async def cb_interest(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":")[2]
    d = await state.get_data()
    sel = list(d.get("interests") or [])
    if key in sel:
        sel.remove(key)
    elif len(sel) >= TG.TGA_MAX_INTERESTS:
        await cb.answer(f"الحد {TG.TGA_MAX_INTERESTS} اهتمامات", show_alert=True)
        return
    else:
        sel.append(key)
    await state.update_data(interests=sel)
    await _edit(cb, T.TGA_INTERESTS.format(max=TG.TGA_MAX_INTERESTS, selected=_interests_label(sel)), K.tga_interests(sel))
    await cb.answer()


@router.callback_query(TgAds.choosing, F.data == "tga:int_done")
async def cb_interests_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("interests"):
        await cb.answer("اختر اهتماماً واحداً على الأقل", show_alert=True)
        return
    await _after_targeting(cb, state)
    await cb.answer()


@router.callback_query(TgAds.choosing, F.data.startswith("tga:ctry_page:"))
async def cb_country_page(cb: CallbackQuery, state: FSMContext) -> None:
    await _edit(cb, T.TGA_GEO, K.tga_country(more=cb.data.endswith(":1")))
    await cb.answer()


@router.callback_query(TgAds.choosing, F.data.startswith("tga:ctry:"))
async def cb_country(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    if code != "any" and code not in TG.COUNTRY_BY_CODE:
        await cb.answer()
        return
    await state.update_data(country=code)
    label = TG.country_label(code) if code != "any" else "🌍 كل الدول"
    await _edit(cb, T.TGA_LANG.format(country=label), K.tga_lang())
    await cb.answer()


@router.callback_query(TgAds.choosing, F.data.startswith("tga:lang:"))
async def cb_lang(cb: CallbackQuery, state: FSMContext) -> None:
    lang = cb.data.split(":")[2]
    if lang not in TG.TGA_LANG_NAME:
        await cb.answer()
        return
    await state.update_data(language=lang)
    await _after_targeting(cb, state)
    await cb.answer()


async def _after_targeting(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(target, state, new_message=new_message)
        return
    await _show_text(target, state, new_message)


# ───────────── TA3 النص ─────────────

async def _show_text(target, state: FSMContext, new_message: bool = False) -> None:
    await state.set_state(TgAds.text)
    await _send(target, T.TGA_TEXT, K.tga_text_input(fmt(P.ADDONS["copy"]["price"])), new_message)


@router.message(TgAds.text, F.text)
async def msg_text(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = " ".join(message.text.split())
    n = len(txt)
    if n < 10:
        await message.answer(T.TGA_TEXT_TOO_SHORT)
        return
    if n > 160:
        await message.answer(T.TGA_TEXT_TOO_LONG.format(n=n))
        return
    d = await state.get_data()
    addons = [a for a in (d.get("addons") or []) if a != "copy"]
    await state.update_data(text=txt, addons=addons)
    await message.answer(T.TGA_TEXT_OK.format(n=n))
    if d.get("editing"):
        await _show_summary(message, state, new_message=True)
        return
    await _show_link(message, state, new_message=True)


@router.callback_query(TgAds.text, F.data == "tga:addon:copy")
async def cb_addon_copy(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    addons = list(d.get("addons") or [])
    if "copy" not in addons:
        addons.append("copy")
    await state.update_data(addons=addons, text=None)
    await cb.answer("✍️ كاتبنا يصيغ لك النص — يُسلَّم قبل إنشاء الإعلان")
    if d.get("editing"):
        await _show_summary(cb, state)
        return
    await _show_link(cb, state)


# ───────────── TA4 الرابط ─────────────

async def _show_link(target, state: FSMContext, new_message: bool = False) -> None:
    await state.set_state(TgAds.link)
    await _send(target, T.TGA_LINK, K.tga_text_step("tga:back:text"), new_message)


@router.message(TgAds.link, F.text)
async def msg_link(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    link = clean_tme(message.text)
    if not link:
        await message.answer(T.TGA_LINK_INVALID)
        return
    await state.update_data(link=link)
    await _show_summary(message, state, new_message=True)


# ───────────── TA5 الملخص ─────────────

def _spec_from_state(d: dict) -> dict:
    return {
        "kind": "tg_ads", "budget": d["budget"], "target_mode": d.get("target_mode", "expert"),
        "channels": d.get("channels") or [], "interests": d.get("interests") or [],
        "country": d.get("country"), "language": d.get("language"),
        "text": d.get("text"), "link": d.get("link"), "addons": d.get("addons") or [],
        "tg_username": V.clean_username(d.get("tg_username")),
    }


def summary_text(spec: dict, price: Decimal, balance: Decimal) -> tuple[str, bool, Decimal]:
    ok = balance >= price
    gap = money(price - balance) if not ok else Decimal("0")
    addons = ""
    if "copy" in (spec.get("addons") or []):
        addons = f"\n✍️ إضافات: <b>نص إعلاني</b> (+{fmt(P.ADDONS['copy']['price'])})"
    text_line = f"<i>{T.esc(spec['text'])}</i> ({len(spec['text'])} حرفاً)" if spec.get("text") else "<b>يكتبه فريقنا</b> ✍️"
    footer = ("بالضغط على «تأكيد» يُخصم المبلغ من رصيدك ويبدأ فريقنا بإنشاء الإعلان." if ok
              else T.META_SUMMARY_GAP.format(gap=fmt(gap)))
    text = T.TGA_SUMMARY.format(
        budget=fmt(spec["budget"]), targeting=T.esc(TG.tga_targeting_label(spec)), text=text_line,
        link=T.esc(spec.get("link")) or "—", addons=addons, price=fmt(price), balance=fmt(balance), footer=footer,
    )
    return text, ok, gap


async def _show_summary(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(TgAds.choosing)
    await CO.ensure_token(state)   # رمز الشراء — يمنع الخصم المكرر (v0.9.2)
    await state.update_data(editing=False, tg_username=target.from_user.username)
    d["tg_username"] = target.from_user.username
    spec = _spec_from_state(d)
    _, price, cost = orders_svc.compute_prices(spec)
    uid = target.from_user.id
    balance = await users_repo.get_balance(uid)
    text, ok, gap = summary_text(spec, price, balance)
    kb = K.tga_summary(ok, fmt(price), fmt(gap) if not ok else None)
    if not ok:
        days_valid = int(await settings_repo.get("order_draft_days", 7))
        draft = await orders_repo.save_awaiting(uid, spec, price, cost, days_valid, d.get("draft_id"), kind="tg_ads")
        await state.update_data(draft_id=draft["id"], gap_usd=str(gap))
    await _send(target, text, kb, new_message)


@router.callback_query(TgAds.choosing, F.data == "tga:edit")
async def cb_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(editing=True)
    await _edit(cb, "✏️ <b>شو بدك تعدّل؟</b>", K.tga_edit_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("tga:back:"))
async def cb_back(cb: CallbackQuery, state: FSMContext) -> None:
    where = cb.data.split(":")[2]
    d = await state.get_data()
    if d.get("kind") != "tg_ads":
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    if where == "budget":
        await state.set_state(TgAds.budget)
        await _edit(cb, T.tga_intro_note(await _hours()), K.tga_budget())
    elif where == "mode":
        await _show_mode(cb, state)
    elif where == "country":
        await state.set_state(TgAds.choosing)
        await _edit(cb, T.TGA_GEO, K.tga_country())
    elif where == "text":
        await _show_text(cb, state)
    elif where == "link":
        await _show_link(cb, state)
    elif where == "summary":
        await _show_summary(cb, state)
    await cb.answer()


@router.callback_query(F.data == "tga:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("draft_id"):
        await orders_repo.cancel_awaiting(d["draft_id"], cb.from_user.id)
    await state.clear()
    await _edit(cb, "تم إلغاء الطلب — ما انخصم شي ✅", K.home_only())
    await cb.answer()


# ───────────── التأكيد ─────────────

@router.callback_query(TgAds.choosing, F.data == "tga:confirm")
async def cb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("budget") or not d.get("link") or not (d.get("text") or "copy" in (d.get("addons") or [])):
        await cb.answer("الطلب ناقص — راجع الملخص", show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    d["tg_username"] = cb.from_user.username
    spec = _spec_from_state(d)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    try:
        order = await orders_svc.confirm(cb.from_user.id, spec, d.get("draft_id"), CO.key_for(cb, d))
    except InsufficientBalance:
        await cb.answer(T.META_INSUFFICIENT_RACE, show_alert=True)
        await _show_summary(cb, state)
        return
    if order.get("duplicate"):
        # ضغطة مكررة/متزامنة: الطلب الأول أُنشئ وخُصم مرة واحدة — لا نكرر الإشعارات ولا الملفات
        await CO.answer_duplicate(cb, order)
        return
    await state.clear()
    await cb.answer("✅ تم الخصم — استلمنا طلبك")
    balance = await users_repo.get_balance(cb.from_user.id)
    text = T.TGA_DONE.format(id=order["id"], price=fmt(order["price_usd"]), balance=fmt(balance), hours=await _hours())
    sent = None
    try:
        await cb.message.edit_text(text, reply_markup=K.meta_done(order["id"]))
        sent = cb.message
    except Exception:  # noqa: BLE001
        sent = await cb.message.answer(text, reply_markup=K.meta_done(order["id"]))
    if sent:
        await orders_repo.set_messages(order["id"], user_msg_id=sent.message_id)
    from app.services import order_notify
    await order_notify.notify_admins_new_order(cb.bot, order["id"])


# ───────────── استئناف مسودة تيليغرام (يستدعيه ord:resume) ─────────────

async def resume_draft(cb: CallbackQuery, state: FSMContext, draft: dict) -> None:
    spec = draft["spec"]
    await state.clear()
    await state.update_data(
        kind="tg_ads", budget=spec["budget"], target_mode=spec.get("target_mode", "expert"),
        channels=spec.get("channels") or [], interests=spec.get("interests") or [], country=spec.get("country"),
        language=spec.get("language"), text=spec.get("text"), link=spec.get("link"), addons=spec.get("addons") or [],
        draft_id=draft["id"],
    )
    try:
        await cb.message.answer(T.META_DRAFT_RESUME.format(id=draft["id"]))
    except Exception:  # noqa: BLE001
        pass
    await _show_summary(cb, state, new_message=True)


async def renew_from(cb: CallbackQuery, state: FSMContext, order: dict) -> None:
    spec = order["spec"]
    await state.clear()
    await state.update_data(
        kind="tg_ads", budget=spec["budget"], target_mode=spec.get("target_mode", "expert"),
        channels=spec.get("channels") or [], interests=spec.get("interests") or [], country=spec.get("country"),
        language=spec.get("language"), text=spec.get("text"), link=spec.get("link"),
        addons=[a for a in (spec.get("addons") or []) if a != "copy"], renew_of=order["id"],
    )
    await _show_summary(cb, state, new_message=True)


# ───────────── النص البديل بعد طلب تعديل ─────────────

class TgRevise(StatesGroup):
    text = State()


@router.callback_query(F.data.startswith("tga:revise:"))
async def cb_revise(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await orders_repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id or o["status"] != "needs_revision":
        await cb.answer("هذا الطلب لا ينتظر تعديلاً الآن", show_alert=True)
        return
    await state.set_state(TgRevise.text)
    await state.update_data(oid=oid)
    await cb.message.answer(T.TGA_REVISION_PROMPT.format(id=oid, reason=T.esc(o.get("revision_note") or "—"),
                                                         text=T.esc(o["spec"].get("text") or "—")),
                            reply_markup=K.cancel_input("nav:orders"))
    await cb.answer()


@router.message(TgRevise.text, F.text)
async def msg_revise(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = " ".join(message.text.split())
    if len(txt) < 10:
        await message.answer(T.TGA_TEXT_TOO_SHORT)
        return
    if len(txt) > 160:
        await message.answer(T.TGA_TEXT_TOO_LONG.format(n=len(txt)))
        return
    d = await state.get_data()
    await state.clear()
    o = await orders_svc.apply_revision(d["oid"], message.from_user.id, txt)
    if not o:
        await message.answer("هذا الطلب لا ينتظر تعديلاً الآن.")
        return
    await message.answer(T.TGA_REVISION_SAVED.format(id=o["id"]), reply_markup=K.order_view({**o, "media_count": 0}))
    from app.services import order_notify
    await order_notify.refresh_admin_cards(message.bot, o["id"])
    await order_notify.notify_admins_text(message.bot, f"✏️ <b>#ORD-{o['id']}</b>: العميل أرسل نصاً جديداً — راجع البطاقة.")
