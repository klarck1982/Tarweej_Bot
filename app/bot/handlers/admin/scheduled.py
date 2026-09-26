"""➕ إدخال أزواج باقة التصميم اليومي (تصميم + نصّه) من خاص الأدمن — بطاقات تأكيد بالمعاينة.

    adm:sub:{id}:add    ← أرسل الأزواج (صورة وتحتها النص) — كل رسالة زوج جاهز
    adm:sub:{id}:queue  ← استعراض الطابور كما سيُرسل مع 🗑️
    adm:sub:{id}:now    ← ▶️ أرسل التالي الآن
    adm:sub:{id}:pause/resume/cancel ← تحكم سريع
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.config import settings
from app.bot.handlers.admin import _common as C
from app.services import scheduled as SD
from app.services.pricing import fmt

log = logging.getLogger("admin_scheduled")

router = Router(name="admin_scheduled")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class ScheduledAdmin(StatesGroup):
    pairs = State()        # يستقبل الأزواج (صورة+نص)
    pair_text = State()    # أرسل صورة بلا نص ← ينتظر النص


def _cancel(message: Message) -> bool:
    return bool(message.text and (message.text.startswith("/") or message.text in ("🏠 القائمة", "❌ إلغاء", "✅ تم")))


def _file(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.document:
        return "document", message.document.file_id
    if message.video:
        return "video", message.video.file_id
    return None


def sub_card_text(sub: dict) -> str:
    st = {"scheduled": "🟢 تعمل", "paused": "⏸️ متوقفة", "completed": "✅ مكتملة",
          "refunded": "↩️ مستردة", "cancelled": "🚫 ملغاة", "awaiting_assets": "📝 جديدة"}.get(sub["status"], sub["status"])
    buf = {"low": f"🔴 احتياطي {sub.get('ready', 0)}", "warn": f"🟡 احتياطي {sub.get('ready', 0)}",
           "ok": f"🟢 احتياطي {sub.get('ready', 0)}"}.get(sub.get("buffer", "ok"), "")
    return (
        f"📅 <b>SUB-{sub['id']}</b> · {SD._esc(sub.get('package_title'))}\n"
        f"👤 {SD._esc(sub.get('label'))} · {('📣 ' + SD._esc(sub.get('target_title'))) if sub.get('target_title') else '💬 محادثة'}\n"
        f"📦 {sub.get('sent_items', sub.get('sent_count', 0))}/{sub['total_items']} · متبقٍ {sub.get('remaining', 0)} · {buf}\n"
        f"⏰ الساعة {str(sub.get('send_time'))[:5]} · {st}"
    )


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):add$"))
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    sid = int(cb.data.split(":")[2])
    sub = await SD.detail(sid)
    if not sub:
        await cb.answer("الاشتراك غير موجود", show_alert=True)
        return
    await C.ask_input(
        cb, state, ScheduledAdmin.pairs, {"subscription_id": sid},
        f"📤 <b>إضافة أزواج SUB-{sid}</b> — {SD._esc(sub.get('label'))}\n\n"
        "أرسل كل تصميم <b>ونصّه تحته</b> (كما سيصل الزبون تماماً) — كل رسالة = زوج جاهز 📥\n"
        "أرسل <b>✅ تم</b> عندما تنتهي، أو «❌ إلغاء».",
        K.admin_sub_input_bar(sid))


@router.message(ScheduledAdmin.pairs, F.photo | F.video | F.document)
async def msg_pair_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sid = int(data.get("subscription_id") or 0)
    file_info = _file(message)
    if not file_info:
        await message.answer("أرسل التصميم كصورة أو ملف أو فيديو.")
        return
    kind, file_id = file_info
    caption = (message.caption or "").strip()
    if not caption:
        await state.update_data(file_kind=kind, file_id=file_id)
        await state.set_state(ScheduledAdmin.pair_text)
        await message.answer("✍️ ممتازة! أرسل الآن نصّها (مع الإيموجي) ليكتمل الزوج:")
        return
    await _save_pair(message, state, sid, kind, file_id, caption)


@router.message(ScheduledAdmin.pair_text, F.text)
async def msg_pair_text(message: Message, state: FSMContext) -> None:
    if _cancel(message):
        await state.clear()
        await message.answer("تم الإلغاء — لم يُحفظ نصف زوج.", reply_markup=K.home_bar())
        return
    data = await state.get_data()
    sid = int(data.get("subscription_id") or 0)
    await _save_pair(message, state, sid, str(data.get("file_kind")), str(data.get("file_id")), (message.text or "").strip())
    await state.set_state(ScheduledAdmin.pairs)


@router.message(ScheduledAdmin.pairs, F.text)
async def msg_pairs_text(message: Message, state: FSMContext) -> None:
    if _cancel(message):
        sid = int((await state.get_data()).get("subscription_id") or 0)
        await state.clear()
        await message.answer(f"✅ انتهى الإدخال — SUB-{sid}", reply_markup=K.home_bar())
        return
    await message.answer("أرسل التصميم كصورة ونصّه تحته (caption) 📎 — أو «✅ تم» للانتهاء.")


@router.message(ScheduledAdmin.pair_text)
async def msg_pair_text_wrong(message: Message) -> None:
    await message.answer("أرسل النص الكتابي برسالة نصية ✍️")


async def _save_pair(message: Message, state: FSMContext, sid: int, kind: str, file_id: str, text: str) -> None:
    try:
        item, alert = await SD.add_pair(sid, kind, file_id, text)
    except ValueError as e:
        await message.answer(f"⚠️ {SD._esc(str(e))}")
        return
    except Exception:  # noqa: BLE001
        log.exception("pair save failed sub=%s", sid)
        await message.answer("تعذر حفظ الزوج — حاول مرة أخرى.")
        return
    sub = await SD.detail(sid)
    # بطاقة الحفظ بالمعاينة الحرفية (ما تراه = ما سيصل)
    try:
        if kind == "photo":
            await message.bot.send_photo(message.chat.id, file_id,
                                         caption=f"✅ حُفظ زوج {item['seq']} — {SD._esc(sub.get('label'))}\n✍️ {SD._esc(text)}",
                                         reply_markup=K.admin_pair_bar(sid, item["seq"]))
        elif kind == "video":
            await message.bot.send_video(message.chat.id, file_id,
                                         caption=f"✅ حُفظ زوج {item['seq']} — {SD._esc(sub.get('label'))}\n✍️ {SD._esc(text)}",
                                         reply_markup=K.admin_pair_bar(sid, item["seq"]))
        else:
            await message.bot.send_document(message.chat.id, file_id,
                                            caption=f"✅ حُفظ زوج {item['seq']} — {SD._esc(sub.get('label'))}\n✍️ {SD._esc(text)}",
                                            reply_markup=K.admin_pair_bar(sid, item["seq"]))
    except Exception:  # noqa: BLE001
        await message.answer(f"✅ حُفظ زوج {item['seq']} — {SD._esc(sub.get('label'))}",
                             reply_markup=K.admin_pair_bar(sid, item["seq"]))
    if sub:
        await message.answer(sub_card_text(sub))
    if alert:
        try:
            from app.services import order_notify as ON
            await ON.notify_admins_text(message.bot, alert)
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):queue$"))
async def cb_queue(cb: CallbackQuery, state: FSMContext) -> None:
    sid = int(cb.data.split(":")[2])
    await state.clear()
    sub = await SD.detail(sid)
    if not sub:
        await cb.answer("غير موجود", show_alert=True)
        return
    pending = [i for i in (sub.get("items") or []) if i["status"] in ("pending", "sending", "failed")]
    if not pending:
        await cb.answer("لا أزواج جاهزة — أرسل دفعة!", show_alert=True)
        return
    await cb.answer()
    for i in pending[:10]:
        cap = (f"👁️ زوج {i['seq']} · {SD._esc(sub.get('label'))}\n{SD._esc(i.get('copy_text'))}"
               f"\n{('⚠️ فشل: ' + SD._esc(str(i.get('last_error'))[:80])) if i.get('last_error') else ''}")
        try:
            if i["file_kind"] == "photo":
                await cb.bot.send_photo(cb.from_user.id, i["file_id"], caption=cap,
                                        reply_markup=K.admin_pair_bar(sid, i["seq"]))
            elif i["file_kind"] == "video":
                await cb.bot.send_video(cb.from_user.id, i["file_id"], caption=cap,
                                        reply_markup=K.admin_pair_bar(sid, i["seq"]))
            else:
                await cb.bot.send_document(cb.from_user.id, i["file_id"], caption=cap,
                                           reply_markup=K.admin_pair_bar(sid, i["seq"]))
        except Exception:  # noqa: BLE001
            await cb.bot.send_message(cb.from_user.id, cap, reply_markup=K.admin_pair_bar(sid, i["seq"]))


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):del:(\d+)$"))
async def cb_del_pair(cb: CallbackQuery) -> None:
    parts = cb.data.split(":")
    sid, seq = int(parts[2]), int(parts[3])
    ok = await SD.delete_pair(sid, seq)
    await cb.answer("🗑️ حُذف الزوج" if ok else "لا يمكن حذفه (أُرسل أو غير موجود)", show_alert=not ok)
    if ok:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
            await cb.message.edit_caption(caption=f"🗑️ حُذف زوج {seq} من SUB-{sid}")
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):now$"))
async def cb_send_now(cb: CallbackQuery, state: FSMContext) -> None:
    sid = int(cb.data.split(":")[2])
    await state.clear()
    await cb.answer("جارٍ الإرسال…")
    result, info = await SD.deliver_next(cb.bot, sid, manual=True)
    if result == "sent":
        seq = (info or {}).get("item", {}).get("seq")
        await cb.answer(f"✅ أُرسل الزوج {seq} الآن", show_alert=True)
    elif result == "empty":
        await cb.answer("لا أزواج جاهزة في الطابور 🤷", show_alert=True)
    else:
        await cb.answer(f"تعذر الإرسال ({result}) — راجع السجل", show_alert=True)


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):cancel_done$"))
async def cb_cancel_done(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await cb.message.edit_text("✅ انتهى الإدخال — يمكنك إضافة دفعة أخرى لاحقاً من Cpanel أو من هنا.")
    except Exception:  # noqa: BLE001 — الرسالة محذوفة؟ نرسل تأكيداً جديداً بدل الخطأ
        await cb.bot.send_message(cb.from_user.id, "✅ انتهى الإدخال.")
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):(pause|resume|cancel)$"))
async def cb_quick_action(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    sid, action = int(parts[2]), parts[3]
    await state.clear()
    if action == "cancel":
        sub, refund = await SD.cancel(sid, admin_id=cb.from_user.id)
        if sub is None:
            await cb.answer("غير موجود", show_alert=True)
            return
        await cb.answer(f"↩️ أُلغي واستُرد {fmt(refund)}$" if refund > 0 else "↩️ أُلغي (اشتراك يدوي — لا مال بالبوت)", show_alert=True)
    else:
        fn = SD.pause if action == "pause" else SD.resume
        sub = await fn(sid)
        await cb.answer("⏸️ أُوقف" if action == "pause" else "▶️ استُؤنف", show_alert=True)
    if sub:
        try:
            await cb.message.edit_text(sub_card_text(SD._view(sub)), reply_markup=K.admin_sub_card(sid))
        except Exception:  # noqa: BLE001
            await cb.bot.send_message(cb.from_user.id, sub_card_text(SD._view(sub)), reply_markup=K.admin_sub_card(sid))
