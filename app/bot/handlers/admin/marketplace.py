"""💼 إدارة سوق القنوات (adm:mp:*) — اعتماد القنوات، طلبات السحب، البلاغات، والإعدادات."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import mp_keyboards as KB
from app.bot import mp_texts as TX
from app.bot import texts as T
from app.bot.handlers.admin import _common as C
from app.config import settings
from app.db.repo import orders as orders_repo, partner_channels as PC, users as users_repo
from app.services import marketplace as MP
from app.services import mp_notify as MN
from app.services import order_notify as ON
from app.services.marketplace import MPError
from app.services.pricing import fmt

router = Router(name="admin_marketplace")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))
esc = T.esc


class AdminMp(StatesGroup):
    reason = State()     # data: mp_act (reject|suspend|paid|payno|cfg), mp_ref


async def _edit(cb: CallbackQuery, text: str, kb=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await C.reply(cb, text, kb)


async def _panel() -> tuple[str, object]:
    c = await MP.admin_counts()
    cfg = await MP.cfg()
    text = TX.ADM_PANEL.format(pending=c["pending"], live=c["live"], total=c["total"], payouts=c["payouts"], payouts_sum=fmt(c["payouts_sum"]),
                               disputes=c["disputes"], posts_month=c["posts_month"], commission=fmt(c["commission_month"]),
                               held=fmt(c["held"]), avail=fmt(c["avail"]), accept=cfg["accept_hours"], hold=cfg["hold_hours"],
                               min=cfg["min_payout"], verify=cfg["verify_hours"], off="" if cfg["enabled"] else "\n🔴 <b>التسجيل متوقف</b>")
    return text, KB.adm_panel(c, cfg["enabled"])


@router.callback_query(F.data == "adm:mp")
async def cb_panel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _panel()
    await _edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data == "adm:mp:onoff")
async def cb_onoff(cb: CallbackQuery) -> None:
    cfg = await MP.cfg()
    await MP.set_cfg("enabled", not cfg["enabled"])
    text, kb = await _panel()
    await _edit(cb, text, kb)
    await cb.answer("🟢 السوق يعمل" if not cfg["enabled"] else "🔴 التسجيل متوقف (الطلبات الجارية تكمل)")


# ───────────── القنوات ─────────────

@router.callback_query(F.data.regexp(r"^adm:mp:list:(pending|all)$"))
async def cb_list(cb: CallbackQuery) -> None:
    which = cb.data.rsplit(":", 1)[1]
    items = await MP.admin_channels("pending" if which == "pending" else None)
    icons = {"pending": "🕐", "approved": "🟢", "rejected": "❌", "suspended": "⛔"}
    rows = [(f"adm:mp:ch:{ch['id']}", f"{icons.get(ch['mp_status'], '•')} {ch['title'][:28]} · {ch['subscribers']}")
            for ch in items]
    title = "🕐 <b>قنوات بانتظار الموافقة</b>" if which == "pending" else "📋 <b>قنوات السوق</b>"
    await _edit(cb, title + ("" if items else "\n\nلا شيء الآن ✅"), KB.adm_list(rows))
    await cb.answer()


async def _owner_name(uid: int | None) -> str:
    if not uid:
        return ""
    u = await users_repo.get_user(uid)
    if not u:
        return str(uid)
    return f"{u['name']} @{u['username']}" if u.get("username") else u["name"]


@router.callback_query(F.data.regexp(r"^adm:mp:ch:(\d+)$"))
async def cb_channel(cb: CallbackQuery) -> None:
    ch = await PC.get(int(cb.data.rsplit(":", 1)[1]))
    if not ch or not ch.get("owner_user_id"):
        await cb.answer("القناة غير موجودة", show_alert=True)
        return
    await _edit(cb, MN.admin_channel_text(ch, await _owner_name(ch["owner_user_id"]), full=True), KB.adm_channel(ch))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:mp:ok:(\d+)$"))
async def cb_approve(cb: CallbackQuery) -> None:
    ch = await MP.admin_decide_channel(int(cb.data.rsplit(":", 1)[1]), cb.from_user.id, True)
    if not ch:
        await cb.answer("لم يُنفَّذ — القناة ليست بانتظار الموافقة", show_alert=True)
        return
    await _edit(cb, MN.admin_channel_text(ch, await _owner_name(ch["owner_user_id"]), full=True), KB.adm_channel(ch))
    await cb.answer("✅ اعتُمدت")
    try:
        await cb.bot.send_message(ch["owner_user_id"], TX.NOTE_APPROVED.format(title=esc(ch["title"])), reply_markup=KB.back_home())
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.regexp(r"^adm:mp:(no|sus|paid|payno):(\d+)$"))
async def cb_ask_reason(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, act, ref = cb.data.split(":")
    prompts = {"no": TX.ADM_REJECT_ASK, "sus": TX.ADM_REJECT_ASK, "paid": TX.ADM_PAID_ASK, "payno": TX.ADM_PAYNO_ASK}
    await C.ask_input(cb, state, AdminMp.reason, {"mp_act": act, "mp_ref": int(ref)}, prompts[act], KB.adm_cancel())


@router.callback_query(F.data.regexp(r"^adm:mp:res:(\d+)$"))
async def cb_resume(cb: CallbackQuery) -> None:
    ch = await MP.admin_suspend(int(cb.data.rsplit(":", 1)[1]), cb.from_user.id, False)
    if not ch:
        await cb.answer("لم يُنفَّذ", show_alert=True)
        return
    await _edit(cb, MN.admin_channel_text(ch, await _owner_name(ch["owner_user_id"]), full=True), KB.adm_channel(ch))
    await cb.answer("▶️ أُعيدت")
    try:
        await cb.bot.send_message(ch["owner_user_id"], TX.NOTE_RESUMED.format(title=esc(ch["title"])))
    except Exception:  # noqa: BLE001
        pass


# ───────────── السحب ─────────────

@router.callback_query(F.data == "adm:mp:pays")
async def cb_payouts(cb: CallbackQuery) -> None:
    items = await MP.pending_payouts()
    rows = [(f"adm:mp:pay:{p['id']}", f"💸 PAY-{p['id']} · {fmt(p['amount_usd'])} · {MP.PAYOUT_METHODS.get(p['method'], '')}") for p in items]
    await _edit(cb, "💸 <b>طلبات السحب المعلّقة</b>" + ("" if items else "\n\nلا شيء الآن ✅"), KB.adm_list(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:mp:pay:(\d+)$"))
async def cb_payout(cb: CallbackQuery) -> None:
    p = await MP.get_payout(int(cb.data.rsplit(":", 1)[1]))
    if not p:
        await cb.answer("غير موجود", show_alert=True)
        return
    await _edit(cb, MN.payout_text(p), KB.adm_payout(p["id"], p["status"] == "pending"))
    await cb.answer()


# ───────────── البلاغات ─────────────

@router.callback_query(F.data == "adm:mp:dsps")
async def cb_disputes(cb: CallbackQuery) -> None:
    items = await MP.disputes()
    rows = [(f"adm:mp:dsp:{o['id']}", f"⚠️ #ORD-{o['id']} · {(o['spec'] or {}).get('channel_title', '')[:24]}") for o in items]
    await _edit(cb, "⚠️ <b>بلاغات العملاء</b>" + ("" if items else "\n\nلا شيء الآن ✅"), KB.adm_list(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:mp:dsp:(\d+)$"))
async def cb_dispute(cb: CallbackQuery) -> None:
    o = await orders_repo.get(int(cb.data.rsplit(":", 1)[1]))
    if not o or o.get("payout_status") != "disputed":
        await cb.answer("البلاغ حُسم أو غير موجود", show_alert=True)
        return
    await _edit(cb, MN.dispute_text(o), KB.adm_dispute(o["id"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:mp:dsp(r|k):(\d+)$"))
async def cb_dispute_decide(cb: CallbackQuery) -> None:
    refund = cb.data.split(":")[2] == "dspr"
    oid = int(cb.data.rsplit(":", 1)[1])
    o = await MP.resolve_dispute(oid, cb.from_user.id, refund)
    if not o:
        await cb.answer("لم يُنفَّذ — البلاغ حُسم مسبقاً", show_alert=True)
        return
    await cb.answer("↩️ استُرد للعميل" if refund else "✅ رُفض البلاغ")
    await _edit(cb, MN.dispute_text(o) + ("\n\n↩️ <b>حُسم: استرداد للعميل</b>" if refund else "\n\n✅ <b>حُسم: الربح للقناة</b>"),
                KB.adm_list([], back="adm:mp:dsps"))
    if refund:
        bal = await users_repo.get_balance(o["user_id"])
        try:
            await cb.bot.send_message(o["user_id"], f"↩️ <b>#ORD-{oid}: قُبل بلاغك</b> وأُعيد <b>{fmt(o.get('refunded_usd') or o['price_usd'])}</b> "
                                                    f"إلى رصيدك (الآن {fmt(bal)}).")
        except Exception:  # noqa: BLE001
            pass
        await MN.owner_note(cb.bot, o, TX.NOTE_DISPUTE_REFUND.format(id=oid))
    else:
        try:
            await cb.bot.send_message(o["user_id"], TX.DISPUTE_KEPT_CUSTOMER.format(id=oid))
        except Exception:  # noqa: BLE001
            pass
        await MN.owner_note(cb.bot, o, TX.NOTE_DISPUTE_KEPT.format(id=oid))
    await ON.refresh_admin_cards(cb.bot, oid)


# ───────────── الإعدادات ─────────────

@router.callback_query(F.data == "adm:mp:cfg")
async def cb_cfg(cb: CallbackQuery) -> None:
    c = await MP.cfg()
    lines = [f"• {label}: <b>{c[key]}</b>" for key, label in TX.CFG_LABELS.items()]
    await _edit(cb, "⚙️ <b>إعدادات السوق</b>\n\n" + "\n".join(lines), KB.adm_cfg())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:mp:set:(\w+)$"))
async def cb_set(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.rsplit(":", 1)[1]
    if key not in MP.CFG_LIMITS:
        await cb.answer()
        return
    lo, hi = MP.CFG_LIMITS[key]
    await C.ask_input(cb, state, AdminMp.reason, {"mp_act": "cfg", "mp_ref": key},
                      TX.ADM_CFG_ASK.format(label=TX.CFG_LABELS[key], lo=lo, hi=hi), KB.adm_cancel("adm:mp:cfg"))


# ───────────── إدخال نصي مشترك ─────────────

@router.message(AdminMp.reason, F.text)
async def msg_reason(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    d = await state.get_data()
    act, ref = d.get("mp_act"), d.get("mp_ref")
    txt = " ".join(message.text.split())[:200]
    await state.clear()
    bot = message.bot
    if act == "cfg":
        try:
            await MP.set_cfg(ref, txt)
        except MPError as e:
            await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.adm_cfg())
            return
        await message.answer("✅ حُفظ الإعداد", reply_markup=KB.adm_cfg())
        return
    if act in ("no", "sus", "payno") and len(txt) < 3:
        await state.set_state(AdminMp.reason)
        await state.update_data(mp_act=act, mp_ref=ref)
        await message.answer("✍️ اكتب سبباً واضحاً (3 أحرف على الأقل) — سيصل لصاحب القناة:", reply_markup=KB.adm_cancel())
        return
    if act in ("no", "sus"):
        ch = await (MP.admin_decide_channel(int(ref), message.from_user.id, False, txt) if act == "no"
                    else MP.admin_suspend(int(ref), message.from_user.id, True, txt))
        if not ch:
            await message.answer("لم يُنفَّذ — تغيّرت حالة القناة.")
            return
        await message.answer(f"✅ {'رُفضت' if act == 'no' else 'أُوقفت'} «{esc(ch['title'])}» وأُبلغ صاحبها.",
                             reply_markup=KB.adm_channel(ch))
        tpl = TX.NOTE_REJECTED if act == "no" else TX.NOTE_SUSPENDED
        try:
            await bot.send_message(ch["owner_user_id"], tpl.format(title=esc(ch["title"]), reason=esc(txt)))
        except Exception:  # noqa: BLE001
            pass
        return
    if act in ("paid", "payno"):
        note = "" if txt in ("-", "—") else txt
        p = await MP.decide_payout(int(ref), message.from_user.id, act == "paid", note)
        if not p:
            await message.answer("لم يُنفَّذ — الطلب حُسم مسبقاً.")
            return
        await message.answer(f"✅ PAY-{p['id']}: {'حُوّل' if act == 'paid' else 'رُفض وأُعيد المبلغ'}.",
                             reply_markup=KB.adm_payout(p["id"], False))
        await MN.refresh_payout_cards(bot, p)
        if act == "paid":
            text = TX.WD_PAID.format(id=p["id"], amount=fmt(p["amount_usd"]), method=MP.PAYOUT_METHODS.get(p["method"], p["method"]),
                                     note=f"\nرقم العملية: <code>{esc(note)}</code>" if note else "")
        else:
            text = TX.WD_REJECTED.format(id=p["id"], note=esc(note or "—"), amount=fmt(p["amount_usd"]))
        try:
            await bot.send_message(p["user_id"], text, reply_markup=KB.to_earn())
        except Exception:  # noqa: BLE001
            pass
