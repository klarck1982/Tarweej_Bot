"""💼 سوق القنوات — واجهة صاحب القناة (mp:*) + أزرار العميل (mpc:*) + اكتشاف إضافة/إزالة البوت من القنوات.

التسجيل: إضافة البوت مشرفاً (my_chat_member) أو إرسال @معرّف القناة ← تحقق فعلي ← الفئة ← الأسعار ← الوصف ← مراجعة الأدمن.
الطلبات: بطاقة قبول/اعتذار ← اختيار الموعد ← البوت ينشر (marketplace.publish_one) ← يتحقق ← يحذف عند الانتهاء.
الأرباح: عرض + سحب (USDT/شام كاش) + تحويل إلى رصيد إعلانات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from app.bot import mp_keyboards as KB
from app.bot import mp_texts as TX
from app.bot import texts as T
from app.config import settings
from app.db.repo import orders as orders_repo, partner_channels as PC
from app.services import marketplace as MP
from app.services import mp_notify as MN
from app.services import order_notify as ON
from app.services import partner_posts as PP
from app.services.marketplace import MPError
from app.services.pricing import fmt, money

log = logging.getLogger(__name__)
router = Router(name="partner")
esc = T.esc


class MpReg(StatesGroup):
    ref = State()
    p24 = State()
    p48 = State()
    ppin = State()
    blurb = State()


class MpOrder(StatesGroup):
    when = State()
    reason = State()


class MpPay(StatesGroup):
    address = State()
    amount = State()
    cv_amount = State()


async def _edit(cb: CallbackQuery, text: str, kb=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — لم يتغير / رسالة بصورة
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass


def _skip(message: Message) -> bool:
    return bool(message.text) and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS)


async def _err(cb: CallbackQuery, e: Exception) -> None:
    await cb.answer(str(e)[:190], show_alert=True)


# ═════════════════════════ اللوحة ═════════════════════════

def _channel_line(ch: dict) -> str:
    return f"• <b>{esc(ch['title'])}</b>: {MN.channel_status(ch)}"


async def _home(user_id: int) -> tuple[str, object]:
    chans = await MP.my_channels(user_id)
    if not chans:
        return TX.INTRO, KB.intro()
    e = await MP.earnings(user_id)
    c = await MP.owner_counts(user_id)
    text = TX.DASH.format(avail=fmt(e["avail"]), held=fmt(e["held"]), waiting=c["waiting"], running=c["running"], done=c["done"],
                          channels="\n".join(_channel_line(ch) for ch in chans))
    return text, KB.dash(chans, int(c["waiting"]))


@router.callback_query(F.data == "mp:home")
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not (await MP.cfg())["enabled"] and not await MP.my_channels(cb.from_user.id):
        await cb.answer("سوق القنوات متوقف مؤقتاً — عُد قريباً 🙏", show_alert=True)
        return
    text, kb = await _home(cb.from_user.id)
    await _edit(cb, text, kb)
    await cb.answer()


@router.message(Command("partner"))
async def cmd_partner(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _home(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "mp:how")
async def cb_how(cb: CallbackQuery) -> None:
    c = await MP.cfg()
    await _edit(cb, TX.HOW.format(accept=c["accept_hours"], hold=c["hold_hours"], min=c["min_payout"]), KB.back_home())
    await cb.answer()


# ═════════════════════════ التسجيل ═════════════════════════

@router.callback_query(F.data == "mp:add")
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not (await MP.cfg())["enabled"]:
        await cb.answer("التسجيل متوقف مؤقتاً", show_alert=True)
        return
    me = await cb.bot.me()
    await state.set_state(MpReg.ref)
    await _edit(cb, TX.ADD, KB.add(me.username or "bot"))
    await cb.answer()


async def _after_verify(bot, user_id: int, info: dict) -> None:
    """يرسل للمستخدم خطوة الفئة (قناة جديدة) أو بطاقة قناته (مسجّلة مسبقاً)."""
    ch, new = await MP.create_draft(user_id, info)
    if new or ch["mp_status"] in ("draft", "rejected"):
        text = TX.VERIFIED.format(title=esc(ch["title"]), username=esc(ch["username"]), subs=PP.subs_label(ch["subscribers"]),
                                  pin=TX.VERIFIED_PIN_OK if info["can_pin"] else TX.VERIFIED_PIN_NO)
        await bot.send_message(user_id, text, reply_markup=KB.categories(ch["id"]))
    else:
        text, kb = _channel_view(ch)
        await bot.send_message(user_id, TX.ALREADY_YOURS + "\n\n" + text, reply_markup=kb)


@router.message(MpReg.ref)
async def msg_ref(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    ref = None
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "chat", None) is not None:
        ref = origin.chat.id
    elif message.text:
        ref = MP.channel_ref(message.text)
    if ref is None:
        raw = (message.text or "").lower()
        private = "/+" in raw or "joinchat" in raw
        await message.answer((TX.ADD_PRIVATE + "\n\n" if private else "") + TX.ADD_ASK_AGAIN, reply_markup=KB.cancel())
        return
    try:
        info = await MP.verify_channel(message.bot, ref, message.from_user.id)
        await state.clear()
        await _after_verify(message.bot, message.from_user.id, info)
    except MPError as e:
        await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.cancel())


@router.my_chat_member(F.chat.type == "channel")
async def on_my_chat_member(ev: ChatMemberUpdated) -> None:
    await handle_member_event(ev)


@router.callback_query(F.data.regexp(r"^mp:reg:(-?\d+)$"))
async def cb_reg_from_detect(cb: CallbackQuery, state: FSMContext) -> None:
    """الأدمن أضاف البوت لقناة واختار «💼 اعرضها في سوق القنوات» من رسالة الاكتشاف."""
    chat_id = int(cb.data.rsplit(":", 1)[1])
    try:
        info = await MP.verify_channel(cb.bot, chat_id, cb.from_user.id)
    except MPError as e:
        await cb.answer(str(e)[:190], show_alert=True)
        return
    await state.clear()
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await _after_verify(cb.bot, cb.from_user.id, info)


async def handle_member_event(ev: ChatMemberUpdated) -> None:
    """صاحب قناة أضاف البوت مشرفاً ← تحقق وبدء التسجيل في خاصّه. أُزيل البوت ← إيقاف القناة واسترداد الجاري.
    يُستدعى أيضاً من معالج الأدمن (panel) لأن راوتر الأدمن يلتقط أحداث القنوات أولاً."""
    new, old = ev.new_chat_member.status, ev.old_chat_member.status
    uid = ev.from_user.id
    if new == "administrator" and old != "administrator":
        try:
            info = await MP.verify_channel(ev.bot, ev.chat.id, uid)
            await _after_verify(ev.bot, uid, info)
        except MPError as e:
            try:
                await ev.bot.send_message(uid, f"⚠️ {esc(str(e))}", reply_markup=KB.cancel())
            except Exception:  # noqa: BLE001 — لم يبدأ محادثة مع البوت
                pass
        except Exception as e:  # noqa: BLE001
            log.info("mp auto-register from my_chat_member failed: %s", e)
    elif old == "administrator" and new != "administrator":
        ch, refunded = await MP.on_bot_removed(ev.chat.id)
        if not ch:
            return
        for o in refunded:
            await ON.push_user_status(ev.bot, o, reason=o.get("note"))
            await ON.refresh_admin_cards(ev.bot, o["id"])
        refunds = f" واستُرد {len(refunded)} طلب للعملاء" if refunded else ""
        try:
            await ev.bot.send_message(ch["owner_user_id"], TX.NOTE_BOT_REMOVED.format(title=esc(ch["title"]), refunds=refunds))
        except Exception:  # noqa: BLE001
            pass
        await ON.notify_admins_text(ev.bot, TX.ADM_CHANNEL_REMOVED.format(title=esc(ch["title"]), n=len(refunded)))


@router.callback_query(F.data.regexp(r"^mp:cat:(\d+):(\w+)$"))
async def cb_cat(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, cid, code = cb.data.split(":")
    try:
        ch = await MP.update_channel(int(cid), cb.from_user.id, category=code)
    except MPError as e:
        await _err(cb, e)
        return
    if ch["mp_status"] == "draft" and Decimal(str(ch["price_24h"] or 0)) < MP.PRICE_MIN:
        await state.set_state(MpReg.p24)
        await state.update_data(mp_cid=ch["id"], mp_mode="reg")
        await _edit(cb, f"🗂 الفئة: {PC.cat_label(code)} ✅\n\n" + TX.ASK_P24, KB.cancel())
    else:
        text, kb = _channel_view(ch)
        await _edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mp:cats:(\d+)$"))
async def cb_cats(cb: CallbackQuery) -> None:
    try:
        ch = await MP.owner_channel(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    await _edit(cb, "🗂 <b>اختر فئة القناة:</b>", KB.categories(ch["id"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mp:prices:(\d+)$"))
async def cb_prices(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        ch = await MP.owner_channel(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    await state.set_state(MpReg.p24)
    await state.update_data(mp_cid=ch["id"], mp_mode="edit")
    await _edit(cb, TX.ASK_P24, KB.cancel())
    await cb.answer()


@router.message(MpReg.p24, F.text)
async def msg_p24(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    try:
        p = MP.parse_price(message.text)
    except MPError as e:
        await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.cancel())
        return
    await state.update_data(mp_p24=str(p))
    await state.set_state(MpReg.p48)
    await message.answer(TX.ASK_P48, reply_markup=KB.skip("mp:p48skip"))


async def _ask_pin(target: Message, state: FSMContext) -> None:
    await state.set_state(MpReg.ppin)
    await target.answer(TX.ASK_PPIN, reply_markup=KB.skip("mp:pinskip", "⏭️ بدون تثبيت"))


@router.message(MpReg.p48, F.text)
async def msg_p48(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    try:
        p = MP.parse_price(message.text)
    except MPError as e:
        await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.skip("mp:p48skip"))
        return
    await state.update_data(mp_p48=str(p))
    await _ask_pin(message, state)


@router.callback_query(MpReg.p48, F.data == "mp:p48skip")
async def cb_p48skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(mp_p48=None)
    await _ask_pin(cb.message, state)
    await cb.answer()


async def _save_prices(target: Message, bot, user_id: int, state: FSMContext, ppin: Decimal | None) -> None:
    d = await state.get_data()
    cid = int(d["mp_cid"])
    try:
        ch = await MP.owner_channel(cid, user_id)
        note = ""
        if ppin is not None:
            # التثبيت يحتاج صلاحية «تعديل الرسائل» للبوت — نتحقق الآن لا عند النشر
            me = await bot.me()
            bm = await bot.get_chat_member(ch["chat_id"], me.id)
            if not getattr(bm, "can_edit_messages", False):
                ppin, note = None, "\n📌 " + TX.VERIFIED_PIN_NO
        ch = await MP.update_channel(cid, user_id, price_24h=Decimal(d["mp_p24"]),
                                     price_48h=Decimal(d["mp_p48"]) if d.get("mp_p48") else None,
                                     price_pin=ppin, allow_pin=ppin is not None)
    except MPError as e:
        await state.clear()
        await target.answer(f"⚠️ {esc(str(e))}")
        return
    except Exception as e:  # noqa: BLE001 — تعذّر فحص صلاحية التثبيت
        log.info("pin rights check failed: %s", e)
        ch = await MP.update_channel(cid, user_id, price_24h=Decimal(d["mp_p24"]),
                                     price_48h=Decimal(d["mp_p48"]) if d.get("mp_p48") else None, price_pin=None, allow_pin=False)
        note = "\n📌 " + TX.VERIFIED_PIN_NO
    if d.get("mp_mode") == "reg":
        await state.set_state(MpReg.blurb)
        await target.answer(TX.ASK_BLURB + note, reply_markup=KB.skip("mp:blurbskip", "⏭️ تخطٍّ"))
        return
    await state.clear()
    text, kb = _channel_view(ch)
    await target.answer(TX.PRICES_SAVED + note + "\n\n" + text, reply_markup=kb)
    if ch["mp_status"] == "approved":
        await ON.notify_admins_text(bot, f"💼 «{esc(ch['title'])}» عدّل أسعاره: {MN.prices_line(ch)}")


@router.message(MpReg.ppin, F.text)
async def msg_ppin(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    try:
        p = MP.parse_price(message.text)
    except MPError as e:
        await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.skip("mp:pinskip", "⏭️ بدون تثبيت"))
        return
    await _save_prices(message, message.bot, message.from_user.id, state, p)


@router.callback_query(MpReg.ppin, F.data == "mp:pinskip")
async def cb_pinskip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _save_prices(cb.message, cb.bot, cb.from_user.id, state, None)


@router.callback_query(F.data.regexp(r"^mp:blurb:(\d+)$"))
async def cb_blurb(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        ch = await MP.owner_channel(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    await state.set_state(MpReg.blurb)
    await state.update_data(mp_cid=ch["id"], mp_mode="edit")
    await _edit(cb, TX.ASK_BLURB, KB.skip("mp:blurbskip", "⏭️ تخطٍّ"))
    await cb.answer()


async def _after_blurb(target: Message, user_id: int, state: FSMContext, blurb: str | None) -> None:
    d = await state.get_data()
    await state.clear()
    try:
        ch = await MP.owner_channel(int(d["mp_cid"]), user_id)
        if blurb is not None:
            ch = await MP.update_channel(ch["id"], user_id, blurb=blurb)
    except MPError as e:
        await target.answer(f"⚠️ {esc(str(e))}")
        return
    text, kb = _channel_view(ch)
    await target.answer((TX.BLURB_SAVED + "\n\n" if blurb is not None and d.get("mp_mode") == "edit" else "") + text, reply_markup=kb)


@router.message(MpReg.blurb, F.text)
async def msg_blurb(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    await _after_blurb(message, message.from_user.id, state, " ".join(message.text.split())[:200])


@router.callback_query(MpReg.blurb, F.data == "mp:blurbskip")
async def cb_blurbskip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _after_blurb(cb.message, cb.from_user.id, state, None)


def _channel_view(ch: dict) -> tuple[str, object]:
    if ch["mp_status"] in ("draft", "rejected"):
        status = f"\nالحالة: {MN.channel_status(ch)}" if ch["mp_status"] == "rejected" else ""
        text = TX.REVIEW.format(title=esc(ch["title"]), username=esc(ch["username"]), cat=PC.cat_label(ch["category"]),
                                subs=PP.subs_label(ch["subscribers"]),
                                prices=MN.prices_line(ch) if Decimal(str(ch["price_24h"] or 0)) >= MP.PRICE_MIN else "— (لم تُحدَّد)",
                                blurb=esc(ch.get("blurb") or "—"), status=status) + TX.REVIEW_FOOTER
        return text, KB.review(ch["id"])
    r = MP.rating_label(ch)
    text = TX.CHANNEL_VIEW.format(title=esc(ch["title"]), username=esc(ch["username"]), status=MN.channel_status(ch),
                                  subs=PP.subs_label(ch["subscribers"]), cat=PC.cat_label(ch["category"]), prices=MN.prices_line(ch),
                                  done=ch["done_n"], rej=ch["reject_n"], tout=ch["timeout_n"], early=ch["early_n"],
                                  rating=f"\n{r}" if r else "")
    return text, KB.channel(ch)


@router.callback_query(F.data.regexp(r"^mp:ch:(\d+)$"))
async def cb_channel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        ch = await MP.owner_channel(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    text, kb = _channel_view(ch)
    await _edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mp:submit:(\d+)$"))
async def cb_submit(cb: CallbackQuery) -> None:
    try:
        ch = await MP.submit_for_review(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    await _edit(cb, TX.SUBMITTED, KB.back_home())
    await cb.answer("📨 أُرسلت")
    uname = f"{cb.from_user.full_name} @{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
    await MN.notify_admins_channel(cb.bot, ch, uname)


@router.callback_query(F.data.regexp(r"^mp:del:(\d+)$"))
async def cb_delete(cb: CallbackQuery) -> None:
    try:
        await MP.delete_draft(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    text, kb = await _home(cb.from_user.id)
    await _edit(cb, text, kb)
    await cb.answer("🗑️ حُذفت")


@router.callback_query(F.data.regexp(r"^mp:toggle:(\d+)$"))
async def cb_toggle(cb: CallbackQuery) -> None:
    try:
        ch = await MP.owner_toggle(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    text, kb = _channel_view(ch)
    await _edit(cb, text, kb)
    await cb.answer("▶️ ظاهرة للعملاء" if ch["enabled"] else "⏸️ مخفية مؤقتاً")


@router.callback_query(F.data.regexp(r"^mp:resync:(\d+)$"))
async def cb_resync(cb: CallbackQuery) -> None:
    try:
        ch = await MP.owner_channel(int(cb.data.split(":")[2]), cb.from_user.id)
        ch = await MP.resync_subs(cb.bot, ch["id"])
    except MPError as e:
        await _err(cb, e)
        return
    except Exception:  # noqa: BLE001
        await cb.answer("تعذّر الوصول للقناة — تأكد أن البوت ما زال مشرفاً فيها", show_alert=True)
        return
    text, kb = _channel_view(ch)
    await _edit(cb, text, kb)
    await cb.answer(f"👥 {PP.subs_label(ch['subscribers'])}")


# ═════════════════════════ الطلبات ═════════════════════════

def _order_label(o: dict) -> str:
    icon = {"submitted": "📩", "in_progress": "📅", "active": "🟢"}.get(o["status"], "•")
    return f"{icon} #ORD-{o['id']} · {(o['spec'] or {}).get('channel_title', '')[:20]} · {fmt(o['cost_usd'])}"


@router.callback_query(F.data == "mp:orders")
async def cb_orders(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    items = await MP.owner_orders(cb.from_user.id)
    text = "📥 <b>طلبات قنواتي الجارية</b>\n\n" + ("اختر طلباً 👇" if items else "لا طلبات جارية الآن. ستصلك الطلبات الجديدة هنا مباشرة 📩")
    await _edit(cb, text, KB.orders([(o["id"], _order_label(o)) for o in items]))
    await cb.answer()


def _owner_order_text(o: dict) -> str:
    if o["status"] == "submitted":
        return MN.request_text(o)
    spec = o["spec"] or {}
    extra = []
    if o["status"] == "in_progress" and o.get("scheduled_at"):
        extra.append(f"📅 موعد النشر: <b>{MN.when(o['scheduled_at'])}</b>")
    if o.get("started_at"):
        extra.append(f"🕒 نُشر: {MN.when(o['started_at'])}")
    if o["status"] == "active" and o.get("ends_at"):
        extra.append(f"⏳ يحذفه البوت: {MN.when(o['ends_at'])}")
    if o["status"] in ("rejected", "refunded") and o.get("note"):
        extra.append(f"ℹ️ {esc(o['note'])}")
    return TX.ORDER_CARD.format(icon={"in_progress": "📅", "active": "🟢", "completed": "✅"}.get(o["status"], "•"), id=o["id"],
                                status=TX.ORDER_STATUS.get(o["status"], o["status"]), title=esc(spec.get("channel_title")),
                                format=PP.fmt_label(spec.get("format", "24h")), earn=fmt(o["cost_usd"]), extra="\n".join(extra))


async def _my_order(cb: CallbackQuery) -> dict | None:
    try:
        return await MP.owner_order(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return None


@router.callback_query(F.data.regexp(r"^mp:o:(\d+)$"))
async def cb_order(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    o = await _my_order(cb)
    if o:
        await _edit(cb, _owner_order_text(o), KB.order(o))
        await cb.answer()


@router.callback_query(F.data.regexp(r"^mp:o:(\d+):acc$"))
async def cb_accept(cb: CallbackQuery) -> None:
    o = await _my_order(cb)
    if not o:
        return
    if o["status"] != "submitted":
        await cb.answer("الطلب لم يعد بانتظار ردك", show_alert=True)
        await _edit(cb, _owner_order_text(o), KB.order(o))
        return
    spec = o["spec"] or {}
    await _edit(cb, TX.ASK_TIME.format(id=o["id"], when=esc(spec.get("when")) if spec.get("when") else "⚡ أقرب وقت"), KB.times(o["id"]))
    await cb.answer()


async def _publish_now(bot, oid: int) -> None:
    res, o = await MP.publish_one(bot, oid)
    if res == "published":
        await MN.on_published(bot, o)
    elif res == "retry":
        await MN.on_publish_retry(bot, o)
    elif res == "refunded":
        await MN.on_refunded(bot, o, "publish")


async def _do_accept(target: Message, bot, owner_id: int, oid: int, when: datetime, now_: bool) -> None:
    try:
        o = await MP.owner_accept(oid, owner_id, when)
    except MPError as e:
        await target.answer(f"⚠️ {esc(str(e))}")
        return
    hours = int((o["spec"] or {}).get("hours") or 24)
    await target.answer(TX.ACCEPTED.format(id=oid, when="الآن" if now_ else MN.when(when), hours=hours),
                        reply_markup=None if now_ else KB.order(o))
    if now_:
        await _publish_now(bot, oid)
    else:
        await ON.push_user_status(bot, o)
        await ON.refresh_admin_cards(bot, oid)


@router.callback_query(F.data.regexp(r"^mp:o:(\d+):t:(now|1|3|21|x)$"))
async def cb_time(cb: CallbackQuery, state: FSMContext) -> None:
    o = await _my_order(cb)
    if not o:
        return
    code = cb.data.rsplit(":", 1)[1]
    if o["status"] != "submitted":
        await cb.answer("الطلب لم يعد بانتظار ردك", show_alert=True)
        return
    if code == "x":
        await state.set_state(MpOrder.when)
        await state.update_data(mp_oid=o["id"])
        await _edit(cb, TX.ASK_TIME_TYPE.format(tz=settings.tz), KB.cancel())
        await cb.answer()
        return
    if code == "now":
        when = MP.now()
    elif code in ("1", "3"):
        when = MP.now() + timedelta(hours=int(code))
    else:   # 21:00 اليوم، أو غداً إن مضت
        when = PP.parse_when("21:00", _tz()) or (MP.now() + timedelta(hours=1))
    await cb.answer("✅")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await _do_accept(cb.message, cb.bot, cb.from_user.id, o["id"], when, code == "now")


def _tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(settings.tz)
    except Exception:  # noqa: BLE001
        from datetime import timezone
        return timezone.utc


@router.message(MpOrder.when, F.text)
async def msg_when(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    when = PP.parse_when(message.text, _tz())
    if not when:
        await message.answer(TX.BAD_TIME, reply_markup=KB.cancel())
        return
    d = await state.get_data()
    await state.clear()
    now_ = when <= MP.now() + timedelta(minutes=1)
    await _do_accept(message, message.bot, message.from_user.id, int(d["mp_oid"]), when, now_)


@router.callback_query(F.data.regexp(r"^mp:o:(\d+):now$"))
async def cb_now(cb: CallbackQuery) -> None:
    o = await _my_order(cb)
    if not o:
        return
    try:
        await MP.owner_reschedule_now(o["id"], cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    await cb.answer("⚡ جاري النشر…")
    await _publish_now(cb.bot, o["id"])
    o = await orders_repo.get(o["id"])
    await _edit(cb, _owner_order_text(o), KB.order(o))


@router.callback_query(F.data.regexp(r"^mp:o:(\d+):rej$"))
async def cb_reject(cb: CallbackQuery) -> None:
    o = await _my_order(cb)
    if not o:
        return
    if o["status"] not in ("submitted", "in_progress"):
        await cb.answer("لا يمكن الاعتذار الآن", show_alert=True)
        return
    await _edit(cb, TX.ASK_REJECT.format(id=o["id"]), KB.reasons(o["id"]))
    await cb.answer()


async def _do_reject(target: Message, bot, owner_id: int, oid: int, reason: str) -> None:
    try:
        o = await MP.owner_reject(oid, owner_id, reason)
    except MPError as e:
        await target.answer(f"⚠️ {esc(str(e))}")
        return
    await target.answer(TX.REJECTED_OWNER.format(id=oid), reply_markup=KB.back_home())
    await MN.on_refunded(bot, o, "owner")


@router.callback_query(F.data.regexp(r"^mp:o:(\d+):r:(\w+)$"))
async def cb_reason(cb: CallbackQuery, state: FSMContext) -> None:
    o = await _my_order(cb)
    if not o:
        return
    code = cb.data.rsplit(":", 1)[1]
    if code == "x":
        await state.set_state(MpOrder.reason)
        await state.update_data(mp_oid=o["id"])
        await _edit(cb, TX.ASK_REJECT_TYPE, KB.cancel())
        await cb.answer()
        return
    if code not in MP.REJECT_REASONS:
        await cb.answer()
        return
    await cb.answer()
    await _do_reject(cb.message, cb.bot, cb.from_user.id, o["id"], MP.REJECT_REASONS[code])


@router.message(MpOrder.reason, F.text)
async def msg_reason(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    reason = " ".join(message.text.split())[:150]
    if len(reason) < 3:
        await message.answer(TX.ASK_REJECT_TYPE, reply_markup=KB.cancel())
        return
    d = await state.get_data()
    await state.clear()
    await _do_reject(message, message.bot, message.from_user.id, int(d["mp_oid"]), reason)


# ═════════════════════════ الأرباح ═════════════════════════

async def _earn_view(user_id: int) -> tuple[str, object]:
    e = await MP.earnings(user_id)
    c = await MP.cfg()
    pending = ""
    if e["pending"]:
        p = e["pending"]
        pending = TX.EARN_PENDING.format(amount=fmt(p["amount_usd"]), method=MP.PAYOUT_METHODS.get(p["method"], p["method"]))
    text = TX.EARN.format(avail=fmt(e["avail"]), held=fmt(e["held"]), paid=fmt(e["paid"]), converted=fmt(e["converted"]),
                          pending=pending, min=c["min_payout"])
    can_wd = e["avail"] >= Decimal(str(c["min_payout"])) and not e["pending"]
    return text, KB.earn(can_wd, e["avail"] > 0)


@router.callback_query(F.data == "mp:earn")
async def cb_earn(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _earn_view(cb.from_user.id)
    await _edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data == "mp:hist")
async def cb_hist(cb: CallbackQuery) -> None:
    rows = await MP.earnings_history(cb.from_user.id)
    lines = []
    for r in rows:
        amt = Decimal(r["amount_usd"])
        where = "محجوز" if r["bucket"] == "held" else "متاح"
        lines.append(f"{TX.HIST_TYPES.get(r['type'], r['type'])} <b>{'+' if amt > 0 else ''}{fmt(amt)}</b> ({where}) · "
                     f"{MN.when(r['created_at'])}")
    await _edit(cb, TX.HISTORY_TITLE + ("\n".join(lines) if lines else TX.HISTORY_EMPTY), KB.to_earn())
    await cb.answer()


@router.callback_query(F.data == "mp:wd")
async def cb_wd(cb: CallbackQuery, state: FSMContext) -> None:
    e = await MP.earnings(cb.from_user.id)
    c = await MP.cfg()
    if e["pending"]:
        await cb.answer("عندك طلب سحب قيد التنفيذ — انتظر حتى يُنجز", show_alert=True)
        return
    if e["avail"] < Decimal(str(c["min_payout"])):
        await cb.answer(f"الحد الأدنى للسحب {c['min_payout']}$ — أرباحك المتاحة {fmt(e['avail'])}", show_alert=True)
        return
    await state.clear()
    await _edit(cb, TX.WD_METHOD.format(avail=fmt(e["avail"])), KB.wd_methods())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mp:wd:m:(\w+)$"))
async def cb_wd_method(cb: CallbackQuery, state: FSMContext) -> None:
    method = cb.data.rsplit(":", 1)[1]
    if method not in MP.PAYOUT_METHODS:
        await cb.answer()
        return
    await state.set_state(MpPay.address)
    await state.update_data(mp_method=method, mp_amount=None)
    await _edit(cb, TX.WD_ADDRESS.format(label=TX.WD_ADDRESS_LABEL[method]), KB.cancel())
    await cb.answer()


async def _wd_confirm(target: Message, user_id: int, state: FSMContext) -> None:
    d = await state.get_data()
    e = await MP.earnings(user_id)
    amount = Decimal(d["mp_amount"]) if d.get("mp_amount") else e["avail"]
    await state.set_state(None)
    await target.answer(TX.WD_CONFIRM.format(amount=fmt(amount), method=MP.PAYOUT_METHODS[d["mp_method"]], address=esc(d["mp_addr"])),
                        reply_markup=KB.wd_confirm(fmt(amount)))


@router.message(MpPay.address, F.text)
async def msg_address(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    d = await state.get_data()
    try:
        addr = MP.clean_address(d.get("mp_method") or "", message.text)
    except MPError as e:
        await message.answer(f"⚠️ {esc(str(e))}", reply_markup=KB.cancel())
        return
    await state.update_data(mp_addr=addr)
    await _wd_confirm(message, message.from_user.id, state)


@router.callback_query(F.data == "mp:wd:amt")
async def cb_wd_amount(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("mp_addr"):
        await cb.answer("انتهت الجلسة — ابدأ السحب من جديد", show_alert=True)
        return
    e = await MP.earnings(cb.from_user.id)
    await state.set_state(MpPay.amount)
    await cb.message.answer(TX.WD_ASK_AMOUNT.format(min=(await MP.cfg())["min_payout"], avail=fmt(e["avail"])), reply_markup=KB.cancel())
    await cb.answer()


def _amount(raw: str) -> Decimal | None:
    try:
        return MP.parse_price(raw)
    except MPError:
        return None


@router.message(MpPay.amount, F.text)
async def msg_wd_amount(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    a = _amount(message.text)
    if a is None:
        await message.answer("اكتب المبلغ رقماً بالدولار، مثل 15", reply_markup=KB.cancel())
        return
    await state.update_data(mp_amount=str(a))
    await _wd_confirm(message, message.from_user.id, state)


@router.callback_query(F.data == "mp:wd:ok")
async def cb_wd_ok(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("mp_addr") or not d.get("mp_method"):
        await cb.answer("انتهت الجلسة — ابدأ السحب من جديد", show_alert=True)
        return
    e = await MP.earnings(cb.from_user.id)
    amount = Decimal(d["mp_amount"]) if d.get("mp_amount") else e["avail"]
    try:
        p = await MP.request_payout(cb.from_user.id, amount, d["mp_method"], d["mp_addr"])
    except MPError as ex:
        await _err(cb, ex)
        return
    await state.clear()
    await _edit(cb, TX.WD_DONE.format(id=p["id"], amount=fmt(p["amount_usd"])), KB.to_earn())
    await cb.answer("📨 أُرسل")
    await MN.notify_admins_payout(cb.bot, p)


@router.callback_query(F.data == "mp:cv")
async def cb_cv(cb: CallbackQuery, state: FSMContext) -> None:
    e = await MP.earnings(cb.from_user.id)
    if e["avail"] <= 0:
        await cb.answer("لا أرباح متاحة للتحويل الآن", show_alert=True)
        return
    await state.clear()
    await state.update_data(mp_cv=None)
    await _edit(cb, TX.CV_CONFIRM.format(amount=fmt(e["avail"])), KB.cv_confirm(fmt(e["avail"])))
    await cb.answer()


@router.callback_query(F.data == "mp:cv:amt")
async def cb_cv_amount(cb: CallbackQuery, state: FSMContext) -> None:
    e = await MP.earnings(cb.from_user.id)
    await state.set_state(MpPay.cv_amount)
    await cb.message.answer(TX.CV_ASK_AMOUNT.format(avail=fmt(e["avail"])), reply_markup=KB.cancel())
    await cb.answer()


@router.message(MpPay.cv_amount, F.text)
async def msg_cv_amount(message: Message, state: FSMContext) -> None:
    if _skip(message):
        await state.clear()
        return
    a = _amount(message.text)
    if a is None:
        await message.answer("اكتب المبلغ رقماً بالدولار، مثل 5", reply_markup=KB.cancel())
        return
    await state.set_state(None)
    await state.update_data(mp_cv=str(a))
    await message.answer(TX.CV_CONFIRM.format(amount=fmt(a)), reply_markup=KB.cv_confirm(fmt(a)))


@router.callback_query(F.data == "mp:cv:ok")
async def cb_cv_ok(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    e = await MP.earnings(cb.from_user.id)
    amount = Decimal(d["mp_cv"]) if d.get("mp_cv") else e["avail"]
    try:
        bal = await MP.convert_to_balance(cb.from_user.id, amount)
    except MPError as ex:
        await _err(cb, ex)
        return
    await state.clear()
    await _edit(cb, TX.CV_DONE.format(amount=fmt(money(amount)), balance=fmt(bal)), KB.to_earn())
    await cb.answer("✅ تم")


# ═════════════════════════ العميل: التقييم والبلاغ ═════════════════════════

@router.callback_query(F.data.regexp(r"^mpc:rateask:(\d+)$"))
async def cb_rate_ask(cb: CallbackQuery) -> None:
    o = await orders_repo.get(int(cb.data.split(":")[2]))
    if not o or o["user_id"] != cb.from_user.id or o["status"] != "completed":
        await cb.answer()
        return
    if o.get("rating"):
        await cb.answer(f"قيّمته مسبقاً: {'⭐' * int(o['rating'])}", show_alert=True)
        return
    await _edit(cb, TX.RATE_ASK.format(title=esc((o["spec"] or {}).get("channel_title")), id=o["id"]), KB.rate(o["id"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mpc:rate:(\d+):([1-5])$"))
async def cb_rate(cb: CallbackQuery) -> None:
    _, _, oid, n = cb.data.split(":")
    try:
        await MP.rate(int(oid), cb.from_user.id, int(n))
    except MPError as e:
        await _err(cb, e)
        return
    await cb.answer(TX.RATE_THANKS)
    from app.bot import keyboards as K
    o = await orders_repo.get(int(oid))
    await _edit(cb, f"{'⭐' * int(n)} {TX.RATE_THANKS}", K.tgp_order_view({**o, "media_count": 0}))


@router.callback_query(F.data.regexp(r"^mpc:dsp:(\d+)$"))
async def cb_dispute(cb: CallbackQuery) -> None:
    o = await orders_repo.get(int(cb.data.split(":")[2]))
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer()
        return
    if o.get("payout_status") != "held" or not o.get("payout_at") or o["payout_at"] <= MP.now():
        await cb.answer("انتهت فترة البلاغات لهذا الطلب أو سبق الإبلاغ عنه — راسلنا من «مساعدة بهذا الطلب»", show_alert=True)
        return
    await _edit(cb, TX.DISPUTE_CONFIRM.format(id=o["id"]), KB.dispute_confirm(o["id"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^mpc:dspok:(\d+)$"))
async def cb_dispute_ok(cb: CallbackQuery) -> None:
    try:
        o = await MP.open_dispute(int(cb.data.split(":")[2]), cb.from_user.id)
    except MPError as e:
        await _err(cb, e)
        return
    from app.bot import keyboards as K
    await _edit(cb, TX.DISPUTE_OPENED.format(id=o["id"]), K.tgp_order_view({**o, "media_count": 0}))
    await cb.answer("📨 أُرسل البلاغ")
    await MN.notify_admins_dispute(cb.bot, o)
    await MN.owner_note(cb.bot, o, TX.NOTE_DISPUTE_OWNER.format(id=o["id"]))


