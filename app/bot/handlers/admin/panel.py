"""A0 — لوحة الإدارة (/admin أو زر 🛠️). الأقسام التفصيلية تأتي في خطواتها:
شحن (2) · مهام وتذاكر وبث وبحث (6) · إعدادات وإحصائيات (6).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import STEP, VERSION
from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db import pool as db
from app.db.repo import settings as settings_repo

router = Router(name="admin")

# كل معالجات هذا الراوتر للأدمن فقط
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


async def _counters() -> dict:
    row = await db.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM users)                                              AS users,
          (SELECT count(*) FROM users  WHERE created_at >= date_trunc('day', now())) AS new_today,
          (SELECT count(*) FROM topups WHERE status = 'pending')                     AS topups,
          (SELECT count(*) FROM tasks  WHERE status IN ('new','in_progress','revision')) AS tasks,
          (SELECT count(*) FROM tickets WHERE status = 'open')                       AS tickets,
          (SELECT count(*) FROM orders WHERE paid_at >= date_trunc('day', now())) AS orders_today,
          (SELECT count(*) FROM orders WHERE status IN ('paid','submitted','in_progress','active','paused')) AS orders_open
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
    text = T.ADMIN_PANEL.format(version=VERSION, step=STEP, mode=mode, users=c["users"], new_today=c["new_today"],
                                topups=c["topups"], tasks=c["tasks"], tickets=c["tickets"], orders_today=c["orders_today"])
    return text, c


@router.message(Command("admin"))
@router.message(F.text == T.BTN_ADMIN)
async def cmd_admin(message: Message) -> None:
    text, c = await _panel_text()
    await message.answer(text, reply_markup=K.admin_panel(c["topups"], c["tasks"], c["tickets"], c["orders_open"], c["attention"]))


@router.callback_query(F.data == "adm:panel")
async def cb_panel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, c = await _panel_text()
    kb = K.admin_panel(c["topups"], c["tasks"], c["tickets"], c["orders_open"], c["attention"])
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


@router.callback_query(F.data == "adm:svcs")
async def cb_settings(cb: CallbackQuery) -> None:
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
    await cb.answer(("تم التفعيل 🟢" if svc[key] else "تم الإيقاف 🔴"))
    await cb_settings(cb)


@router.callback_query(F.data.in_({"adm:tasks", "adm:tickets", "adm:stats", "adm:bc", "adm:find"}))
async def cb_soon(cb: CallbackQuery) -> None:
    step = {"adm:tasks": 6, "adm:tickets": 6, "adm:stats": 6, "adm:bc": 6, "adm:find": 6}[cb.data]
    await cb.answer(f"يُفعَّل في الخطوة {step} من 6", show_alert=True)
