"""A0 — لوحة الإدارة (/admin أو زر 🛠️). الأقسام التفصيلية تأتي في خطواتها:
شحن (2) · مهام وتذاكر وبث وبحث (6) · إعدادات وإحصائيات (6).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import logging

from app import STEP, VERSION
from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db import pool as db
from app.db.repo import events, settings as settings_repo
from app.services import channels as CH
from app.services import cpanel as CP
from app.services import orders as orders_svc

log = logging.getLogger("admin")
router = Router(name="admin")

# كل معالجات هذا الراوتر للأدمن فقط
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.my_chat_member.filter(F.from_user.id.in_(set(settings.admin_ids)))


async def _counters() -> dict:
    row = await db.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM users)                                              AS users,
          (SELECT count(*) FROM users  WHERE created_at >= date_trunc('day', now())) AS new_today,
          (SELECT count(*) FROM topups WHERE status = 'pending')                     AS topups,
          (SELECT count(*) FROM orders WHERE (kind = 'design' AND status IN ('submitted','in_progress','needs_revision'))
                                          OR (kind IN ('tg_post','tg_ads') AND status IN ('submitted','in_progress'))) AS tasks,
          (SELECT count(*) FROM orders WHERE kind = 'design' AND status IN ('submitted','in_progress','needs_revision') AND due_at < now()) AS tasks_late,
          (SELECT count(*) FROM tickets WHERE status = 'open')                       AS tickets,
          (SELECT count(*) FROM orders WHERE paid_at >= date_trunc('day', now())) AS orders_today,
          (SELECT count(*) FROM orders WHERE status IN ('paid','submitted','in_progress','active','paused','needs_revision','delivered')) AS orders_open
        """
    )
    c = dict(row)
    from app.db.repo import orders as orders_repo
    from app.services import nour
    c["attention"] = await orders_repo.count_attention(nour.is_dry_run())
    return c


async def _panel_text() -> tuple[str, dict]:
    c = await _counters()
    from app.services import nour
    mode = f"{settings.mode} · {'🧪 محاكاة نور' if nour.is_dry_run() else '🟢 نور حقيقي'}"
    tasks = str(c["tasks"]) + (f" (🔴 {c['tasks_late']} متأخر)" if c.get("tasks_late") else "")
    text = T.ADMIN_PANEL.format(version=VERSION, step=STEP, mode=mode, users=c["users"], new_today=c["new_today"],
                                topups=c["topups"], tasks=tasks, tickets=c["tickets"], orders_today=c["orders_today"])
    return text, c


@router.message(Command("admin"))
@router.message(F.text == T.BTN_ADMIN)
async def cmd_admin(message: Message) -> None:
    text, c = await _panel_text()
    await message.answer(text, reply_markup=K.admin_panel(c["topups"], c["tasks"], c["tickets"], c["orders_open"], c["attention"], CP.cpanel_url(),
                                                          late=c.get("tasks_late", 0)))


@router.message(Command("cpanel"))
async def cmd_cpanel(message: Message) -> None:
    url = CP.cpanel_url()
    if not url:
        await message.answer(T.CPANEL_LOCAL_ONLY)
        return
    await message.answer(T.CPANEL_OPEN, reply_markup=K.cpanel_open(url))


@router.callback_query(F.data == "adm:panel")
async def cb_panel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, c = await _panel_text()
    kb = K.admin_panel(c["topups"], c["tasks"], c["tickets"], c["orders_open"], c["attention"], CP.cpanel_url(), late=c.get("tasks_late", 0))
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await cb.message.answer(text, reply_markup=kb)  # قادم من رسالة بصورة
    await cb.answer("محدّث ✅")


@router.callback_query(F.data == "adm:settings")
async def cb_settings_menu(cb: CallbackQuery) -> None:
    try:
        await cb.message.edit_text("⚙️ <b>الإعدادات</b>", reply_markup=K.admin_settings_menu())
    except Exception:  # noqa: BLE001
        await cb.message.answer("⚙️ <b>الإعدادات</b>", reply_markup=K.admin_settings_menu())
    await cb.answer()


async def _render_services(cb: CallbackQuery) -> None:
    """شاشة تشغيل/إيقاف الخدمات — بلا cb.answer (يجيب المستدعي مرة واحدة)."""
    svc = await settings_repo.services()
    names = {"meta": "📢 إعلانات Meta", "tg_ads": "📣 Telegram Ads", "tg_post": "📝 قنوات شريكة",
             "addons": "🎨 تصميم وكتابة", "ai_reel": "🤖 ريل سينمائي AI"}
    rows = [[K.ib(f"{'🟢' if svc[k] else '🔴'} {v}", f"adm:svc:{k}", "success" if svc[k] else "danger")]
            for k, v in names.items()]
    rows.append([K.ib("◀️ رجوع", "adm:settings")])
    await cb.message.edit_text(
        "⚙️ <b>تشغيل / إيقاف الخدمات</b>\nاضغط على خدمة لتبديل حالتها — يسري فوراً على كل المستخدمين.",
        reply_markup=K.InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "adm:svcs")
async def cb_settings(cb: CallbackQuery) -> None:
    await _render_services(cb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:svc:"))
async def cb_toggle_service(cb: CallbackQuery) -> None:
    key = cb.data.split(":")[-1]
    svc = await settings_repo.services()
    if key not in svc:
        await cb.answer()
        return
    svc[key] = not svc[key]
    await settings_repo.set_("services", svc)
    await _render_services(cb)
    await cb.answer("تم التفعيل 🟢" if svc[key] else "تم الإيقاف 🔴")   # إجابة واحدة فقط


@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery) -> None:
    url = CP.cpanel_url()
    if url:
        await cb.message.answer("📊 الإحصائيات الكاملة (إيراد، ربح، رسم بياني، أعلى الخدمات) في Cpanel 👇", reply_markup=K.cpanel_open(url))
        await cb.answer()
    else:
        await cb.answer("الإحصائيات في Cpanel — متاحة على الاستضافة فقط", show_alert=True)


# ───────────── 🛠️ لوحة المهام اليدوية ─────────────

async def tasks_view() -> tuple[str, object]:
    from app.db.repo import orders as orders_repo
    from app.services import design as DS, order_notify as ON
    rows = await orders_repo.list_tasks()
    if not rows:
        return T.ADMIN_TASKS_EMPTY, K.admin_tasks_list([])
    data = []
    for o in rows:
        urg = DS.urgency(o) if o.get("kind") == "design" else ""
        if o.get("kind") == "design":
            when = DS.left_label(o.get("due_at"))
        elif o.get("scheduled_at"):
            when = "📅 " + ON._when(o["scheduled_at"])
        else:
            when = "⚡"
        icon = urg or orders_svc.status_icon(o)
        label = f"{icon} #ORD-{o['id']} · {ON.pkg_label(o['spec'])} · {when} · {(o.get('user_name') or '')[:10]}"
        data.append((o["id"], label[:64], "danger" if urg == "🔴" else ("primary" if urg == "🟠" else None)))
    return T.ADMIN_TASKS_LIST.format(n=len(rows)), K.admin_tasks_list(data)


@router.callback_query(F.data == "adm:tasks")
async def cb_tasks(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await tasks_view()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ───────────── إلغاء إدخال نصي (يعمل من أي بطاقة) ─────────────

@router.callback_query(F.data == "adm:cancel_input")
async def cb_cancel_input(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await cb.message.edit_text("أُلغي ✅")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


# ───────────── 📡 قنوات الإدارة ─────────────

async def _channels_view() -> tuple[str, object]:
    cfg = await CH.all_cfg()
    lines = []
    for kind in CH.KINDS:
        ch = cfg.get(kind)
        lines.append(f"{CH.label(kind)}: " + (f"<b>{T.esc(ch['title'])}</b>" if ch and ch.get("id") else "<i>غير مربوطة → تصل إلى خاصّك</i>"))
    return T.ADMIN_CHANNELS.format(rows="\n".join(lines)), K.admin_channels_menu(cfg)


@router.callback_query(F.data == "adm:ch:menu")
async def cb_channels(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _channels_view()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "adm:ch:help")
async def cb_channels_help(cb: CallbackQuery) -> None:
    me = await cb.bot.get_me()
    await cb.message.answer(T.ADMIN_CHANNELS_HELP.format(bot=me.username), reply_markup=K.InlineKeyboardMarkup(
        inline_keyboard=[[K.ib("◀️ رجوع", "adm:ch:menu")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ch:info:"))
async def cb_channel_info(cb: CallbackQuery) -> None:
    kind = cb.data.split(":")[3]
    ch = await CH.get(kind)
    if not ch:
        await cb.answer("غير مربوطة", show_alert=True)
        return
    await cb.message.edit_text(
        f"{CH.label(kind)}\nالقناة: <b>{T.esc(ch['title'])}</b>\nالمعرّف: <code>{ch['id']}</code>",
        reply_markup=K.admin_channel_info(kind))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ch:test:"))
async def cb_channel_test(cb: CallbackQuery) -> None:
    kind = cb.data.split(":")[3]
    ch = await CH.get(kind)
    if not ch:
        await cb.answer("غير مربوطة", show_alert=True)
        return
    try:
        await cb.bot.send_message(ch["id"], f"🧪 رسالة اختبار — هذه {CH.label(kind)} لبوت {T.esc(settings.bot_name)} ✅")
        await cb.answer("وصلت ✅", show_alert=True)
    except Exception as e:  # noqa: BLE001
        await cb.answer(f"فشل الإرسال: {str(e)[:150]}\nتأكد أن البوت ما زال أدمن في القناة.", show_alert=True)


@router.callback_query(F.data.startswith("adm:ch:unbind:"))
async def cb_channel_unbind(cb: CallbackQuery) -> None:
    kind = cb.data.split(":")[3]
    await CH.unset(kind)
    await events.log_event("channel_unbound", cb.from_user.id, kind=kind)
    await cb.answer("فُصلت — البطاقات تعود إلى خاصّك")
    text, kb = await _channels_view()
    await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^adm:ch:bind:(\w+):(-?\d+)$"))
async def cb_channel_bind(cb: CallbackQuery) -> None:
    _, _, _, kind, chat_id = cb.data.split(":")
    chat_id = int(chat_id)
    if kind not in CH.KINDS:
        await cb.answer()
        return
    try:
        chat = await cb.bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
        member = await cb.bot.get_chat_member(chat_id, cb.bot.id)
        ok_states = ("administrator", "creator") + (("member",) if chat.type != "channel" else ())
        if member.status not in ok_states:
            await cb.answer("البوت ليس أدمن في هذه القناة — ارفعه أدمن ثم أعد المحاولة.", show_alert=True)
            return
    except Exception as e:  # noqa: BLE001
        await cb.answer(f"تعذّر الوصول للقناة: {str(e)[:120]}", show_alert=True)
        return
    await CH.set_(kind, chat_id, title)
    await events.log_event("channel_bound", cb.from_user.id, kind=kind, chat_id=chat_id)
    try:
        await cb.bot.send_message(chat_id, T.CHANNEL_BOUND_NOTICE.format(kind=CH.label(kind), bot=T.esc(settings.bot_name)))
    except Exception:  # noqa: BLE001
        pass
    await cb.message.edit_text(T.ADMIN_CHANNEL_BOUND.format(kind=CH.label(kind), title=T.esc(title)),
                               reply_markup=K.InlineKeyboardMarkup(inline_keyboard=[[K.ib("📡 قنوات الإدارة", "adm:ch:menu")]]))
    await cb.answer("تم الربط ✅")


@router.callback_query(F.data.startswith("adm:ch:ignore:"))
async def cb_channel_ignore(cb: CallbackQuery) -> None:
    await cb.message.edit_text("تم التجاهل — يمكنك الربط لاحقاً من ⚙️ إعدادات → 📡 قنوات الإدارة.")
    await cb.answer()


@router.message(F.chat.type == "private", F.forward_origin)
async def on_forward_from_channel(message: Message, state: FSMContext) -> None:
    """احتياط: الأدمن يعيد توجيه أي رسالة من القناة إلى البوت → نعرض الربط (إن فاتته رسالة الاكتشاف)."""
    origin = message.forward_origin
    chat = getattr(origin, "chat", None)
    if not chat or chat.type not in ("channel", "supergroup", "group"):
        return
    if await state.get_state():
        return  # داخل خطوة كتابة — لا نقاطعه
    taken = await CH.all_cfg()
    await message.answer(T.ADMIN_CHANNEL_DETECTED.format(title=T.esc(chat.title or str(chat.id)), id=chat.id),
                         reply_markup=K.admin_channel_bind(chat.id, taken))


@router.my_chat_member()
async def on_my_chat_member(ev: ChatMemberUpdated) -> None:
    """الأدمن أضاف البوت إلى قناة/مجموعة (أو أزاله) — نعرض عليه ربطها فوراً في خاصّه."""
    chat = ev.chat
    if chat.type not in ("channel", "supergroup", "group"):
        return
    new = ev.new_chat_member.status
    old = ev.old_chat_member.status
    admin_id = ev.from_user.id
    ok_states = ("administrator", "creator") + (("member",) if chat.type != "channel" else ())
    if new in ok_states and old not in ok_states:
        taken = await CH.all_cfg()
        try:
            await ev.bot.send_message(admin_id, T.ADMIN_CHANNEL_DETECTED.format(title=T.esc(chat.title or str(chat.id)), id=chat.id),
                                      reply_markup=K.admin_channel_bind(chat.id, taken))
        except Exception as e:  # noqa: BLE001 — الأدمن لم يفتح خاصّ البوت بعد
            log.info("cannot offer channel bind to %s: %s", admin_id, e)
    elif new not in ok_states and old in ok_states:
        kinds = await CH.kinds_using(chat.id)
        for k in kinds:
            await CH.unset(k)
        if kinds:
            try:
                await ev.bot.send_message(admin_id, T.ADMIN_CHANNEL_LOST.format(
                    title=T.esc(chat.title or str(chat.id)), kinds="، ".join(CH.label(k) for k in kinds)))
            except Exception:  # noqa: BLE001
                pass
