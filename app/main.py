"""نقطة الإقلاع.

    MODE=polling  → على جهازك: البوت يسحب التحديثات بنفسه (لا يحتاج رابطاً عاماً).
    MODE=webhook  → على Render: تيليغرام يرسل التحديثات إلى https://<app>.onrender.com/webhook/<secret>

في الحالتين: اتصال بقاعدة البيانات ← ترحيلات ← تسجيل الراوترات ← خادم HTTP على PORT (لـ /health) ← إشعار الأدمن.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app import STEP, VERSION
from app.bot import texts as T
from app.bot.fsm_storage import PostgresStorage
from app.bot.handlers import fallback, menu, meta_wizard, orders, start, topup
from app.bot.wide import WideButtonsMiddleware
from app.bot.handlers.admin import panel as admin_panel
from app.bot.handlers.admin import orders as admin_orders
from app.bot.handlers.admin import topups as admin_topups
from app.bot.middlewares import ErrorsMiddleware, UserMiddleware
from app.config import settings
from app.db import pool as db
from app.db.repo import users as users_repo
from app.services import scheduler
from app.web import make_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("main")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=PostgresStorage())
    dp.update.outer_middleware(ErrorsMiddleware())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())
    # الترتيب مهم: الأدمن أولاً، ثم start، ثم القوائم، وأخيراً fallback يلتقط كل ما تبقّى
    dp.include_router(admin_topups.router)
    dp.include_router(admin_orders.router)
    dp.include_router(admin_panel.router)
    dp.include_router(start.router)
    dp.include_router(meta_wizard.router)   # قبل menu: يلتقط meta:* و ord:resume
    dp.include_router(orders.router)
    dp.include_router(menu.router)
    dp.include_router(topup.router)
    dp.include_router(fallback.router)
    return dp


async def set_commands(bot: Bot) -> None:
    user_cmds = [
        BotCommand(command="start", description="القائمة الرئيسية"),
        BotCommand(command="balance", description="رصيدي"),
        BotCommand(command="orders", description="طلباتي"),
        BotCommand(command="help", description="الدعم"),
        BotCommand(command="cancel", description="إلغاء العملية الحالية"),
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                user_cmds + [BotCommand(command="admin", description="لوحة الإدارة")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:  # noqa: BLE001 — الأدمن لم يضغط Start بعد
            log.info("skip admin commands for %s: %s", admin_id, e)


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            log.info("cannot notify admin %s: %s", admin_id, e)


async def on_startup(bot: Bot, migrations: list[str]) -> None:
    await set_commands(bot)
    me = await bot.get_me()
    users_count = await users_repo.count_users()
    log.info("bot @%s started — v%s step %s mode=%s users=%s admins=%s",
             me.username, VERSION, STEP, settings.mode, users_count, list(settings.admin_ids))
    await notify_admins(bot, T.STARTUP_NOTICE.format(
        bot=settings.bot_name, version=VERSION, step=STEP, mode=settings.mode,
        migrations=", ".join(migrations) if migrations else "لا شيء", users=users_count,
    ) + f"\nالأدمن المحمّلون: {len(settings.admin_ids)}")


async def run() -> None:
    await db.init_pool(settings.database_url)
    migrations = await db.run_migrations()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(WideButtonsMiddleware())  # أزرار بعرض الشاشة (انظر app/bot/wide.py)
    dp = build_dispatcher()
    app = make_app()
    sched = scheduler.start(bot)   # إعادة المحاولات + انتهاء المسودات + مزامنة نور (داخل نفس العملية)
    try:
        await _serve(bot, dp, app, migrations)
    finally:
        sched.cancel()
        from app.services import nour
        await nour.close()
        await bot.session.close()
        await db.close_pool()


async def _serve(bot: Bot, dp: Dispatcher, app: web.Application, migrations: list[str]) -> None:

    if settings.is_webhook:
        # تيليغرام يرسل إلينا — نتحقق من السر في المسار وفي الترويسة معاً
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret).register(
            app, path=settings.webhook_path
        )
        setup_application(app, dp, bot=bot)

        async def _set_webhook(_: web.Application) -> None:
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            log.info("webhook set: %s", settings.webhook_url)
            await on_startup(bot, migrations)

        app.on_startup.append(_set_webhook)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
        await site.start()
        log.info("HTTP listening on 0.0.0.0:%s (webhook mode)", settings.port)
        try:
            await asyncio.Event().wait()  # يعمل إلى الأبد حتى يوقفه Render
        finally:
            await runner.cleanup()
    else:
        # polling محلياً — ونشغّل /health أيضاً حتى يبقى السلوك واحداً
        await bot.delete_webhook(drop_pending_updates=False)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host="0.0.0.0", port=settings.port).start()
        log.info("HTTP listening on 0.0.0.0:%s (polling mode)", settings.port)
        await on_startup(bot, migrations)
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False)
        finally:
            await runner.cleanup()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("bye 👋")
    except TelegramUnauthorizedError:
        log.error("❌ تيليغرام رفض التوكن — تأكد من BOT_TOKEN (انسخه كاملاً من BotFather)")
        sys.exit(1)
    except RuntimeError as e:
        log.error("❌ %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
