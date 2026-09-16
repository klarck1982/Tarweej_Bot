"""A0 — لوحة الإدارة (/admin أو زر 🛠️). الأقسام التفصيلية تأتي في خطواتها:
شحن (2) · مهام وتذاكر وبث وبحث (6) · إعدادات وإحصائيات (6).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
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
          (SELECT count(*) FROM orders WHERE created_at >= date_trunc('day', now()) AND status <> 'draft') AS orders_today
        """
    )
    return dict(row)


async def _panel_text() -> tuple[str, dict]:
    c = await _counters()
    text = T.ADMIN_PANEL.format(version=VERSION, step=STEP, mode=settings.mode, **c)
    return text, c


@router.message(Command("admin"))
@router.message(F.text == T.BTN_ADMIN)
async def cmd_admin(message: Message) -> None:
    text, c = await _panel_text()
    await message.answer(text, reply_markup=K.admin_panel(c["topups"], c["tasks"], c["tickets"]))


@router.callback_query(F.data == "adm:panel")
async def cb_panel(cb: CallbackQuery) -> None:
    text, c = await _panel_text()
    try:
        await cb.message.edit_text(text, reply_markup=K.admin_panel(c["topups"], c["tasks"], c["tickets"]))
    except Exception:  # noqa: BLE001 — "message is not modified" عند عدم تغيّر الأرقام
        pass
    await cb.answer("محدّث ✅")


@router.callback_query(F.data == "adm:settings")
async def cb_settings(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    names = {"meta": "📢 إعلانات Meta", "tg_ads": "📣 Telegram Ads", "tg_post": "📝 قنوات شريكة",
             "addons": "🎨 تصميم وكتابة", "ai_reel": "🤖 ريل سينمائي AI"}
    rows = [[K.ib(f"{'🟢' if svc[k] else '🔴'} {v}", f"adm:svc:{k}")] for k, v in names.items()]
    rows.append([K.ib("◀️ رجوع للوحة", "adm:panel")])
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


@router.callback_query(F.data.in_({"adm:topups", "adm:tasks", "adm:tickets", "adm:stats", "adm:bc", "adm:find"}))
async def cb_soon(cb: CallbackQuery) -> None:
    step = {"adm:topups": 2, "adm:tasks": 6, "adm:tickets": 6, "adm:stats": 6, "adm:bc": 6, "adm:find": 6}[cb.data]
    await cb.answer(f"يُفعَّل في الخطوة {step} من 6", show_alert=True)
