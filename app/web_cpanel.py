"""Cpanel — Mini App داخل تيليغرام على نفس خادم البوت.

    GET  /cpanel                 صفحة الواجهة (HTML واحد، بلا اعتماديات خارجية إلا سكربت تيليغرام الرسمي)
    POST /cpanel/api/snapshot    كل البيانات (يتطلب initData)
    POST /cpanel/api/stats       الإحصائيات لفترة
    POST /cpanel/api/save/<sec>  حفظ قسم: pricing | general | payments | services
    POST /cpanel/api/channel_test  رسالة تجريبية إلى قناة
    POST /cpanel/api/partner/{save|toggle|delete}   القنوات الشريكة (v0.6.0)
    POST /cpanel/api/nour/test      اختبار اتصال Nour Ads الآن (v0.7.0)
    POST /cpanel/api/nour/report    إرسال تقرير المطابقة الآن إلى قناة التنبيهات
    POST /cpanel/api/reset/preview  أرقام ما سيُمسح + هل مسموح
    POST /cpanel/api/reset/execute  🧨 التصفير (كلمة تأكيد «تصفير»)

كل طلب API يحمل ترويسة X-Telegram-Init-Data؛ نتحقق من التوقيع ومن ADMIN_IDS في كل مرة (بلا جلسات).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiogram import Bot
from aiohttp import web

from app.services import cpanel as CP

log = logging.getLogger("cpanel")
_HTML_PATH = Path(__file__).parent / "static" / "cpanel.html"
_MAX_BODY = 256 * 1024


def _auth(request: web.Request) -> dict:
    user = CP.verify_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "auth", "message": "افتح Cpanel من زرّها داخل تيليغرام (انتهت صلاحية الجلسة أو التوقيع غير صالح)."}, ensure_ascii=False),
                                   content_type="application/json")
    if not CP.is_admin(user["id"]):
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden", "message": "هذه اللوحة للإدارة فقط — رقمك ليس ضمن ADMIN_IDS."}, ensure_ascii=False),
                                content_type="application/json")
    return user


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False, default=str), status=status,
                        content_type="application/json", charset="utf-8")


async def _body(request: web.Request) -> dict:
    if request.content_length and request.content_length > _MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=_MAX_BODY, actual_size=request.content_length)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return data if isinstance(data, dict) else {}


async def page(_: web.Request) -> web.Response:
    html = _HTML_PATH.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


async def snapshot(request: web.Request) -> web.Response:
    user = _auth(request)
    snap = await CP.snapshot()
    snap["me"] = {"id": user["id"], "name": user.get("first_name", "")}
    snap["stats"] = await CP.stats("7d")
    return _json(snap)


async def stats(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    return _json(await CP.stats(str(body.get("period", "7d"))))


async def save(request: web.Request) -> web.Response:
    user = _auth(request)
    section = request.match_info["section"]
    body = await _body(request)
    try:
        if section == "pricing":
            changed = await CP.save_pricing(body, user["id"])
        elif section == "general":
            changed = await CP.save_general(CP.validate_general(body), user["id"])
        elif section == "payments":
            changed = await CP.save_payments(body, user["id"])
        elif section == "services":
            changed = await CP.save_services(body, user["id"])
        else:
            return _json({"error": "unknown_section"}, 404)
    except ValueError as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        log.exception("cpanel save %s failed: %s", section, e)
        return _json({"error": "server", "message": "خطأ غير متوقع أثناء الحفظ — حاول مرة أخرى."}, 500)
    log.info("cpanel: %s saved %s (%s changes)", user["id"], section, len(changed))
    snap = await CP.snapshot()
    return _json({"ok": True, "changed": changed, "snapshot": snap})


def make_channel_test(bot: Bot):
    async def channel_test(request: web.Request) -> web.Response:
        _auth(request)
        body = await _body(request)
        kind = str(body.get("kind", ""))
        from app.bot import texts as T
        from app.config import settings
        from app.services import channels as CH
        ch = await CH.get(kind)
        if not ch:
            return _json({"error": "unbound", "message": "هذه القناة غير مربوطة بعد."}, 400)
        try:
            await bot.send_message(ch["id"], f"🧪 رسالة اختبار من Cpanel — هذه {CH.label(kind)} لبوت {T.esc(settings.bot_name)} ✅")
        except Exception as e:  # noqa: BLE001
            return _json({"error": "send", "message": f"فشل الإرسال: {str(e)[:160]} — تأكد أن البوت ما زال مشرفاً في القناة."}, 400)
        return _json({"ok": True})
    return channel_test


async def partner_save(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    try:
        ch = await CP.save_partner_channel(body, user["id"])
    except ValueError as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        log.exception("partner save failed: %s", e)
        return _json({"error": "server", "message": "خطأ غير متوقع أثناء الحفظ — حاول مرة أخرى."}, 500)
    return _json({"ok": True, "channel": ch, "partner": await CP.partner_channels_view(), "service_locked": await CP.service_locks()})


async def partner_toggle(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    try:
        ch = await CP.toggle_partner_channel(int(body.get("id") or 0), bool(body.get("enabled")), user["id"])
    except Exception as e:  # noqa: BLE001
        return _json({"error": "server", "message": str(e)[:160]}, 500)
    if not ch:
        return _json({"error": "missing", "message": "القناة غير موجودة"}, 404)
    return _json({"ok": True, "channel": ch, "partner": await CP.partner_channels_view(), "service_locked": await CP.service_locks()})


async def partner_delete(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    res = await CP.delete_partner_channel(int(body.get("id") or 0), user["id"])
    if res == "missing":
        return _json({"error": "missing", "message": "القناة غير موجودة"}, 404)
    return _json({"ok": True, "result": res, "partner": await CP.partner_channels_view(), "service_locked": await CP.service_locks()})


async def nour_test(request: web.Request) -> web.Response:
    _auth(request)
    from app.services import nour_health as NH
    res = await NH.check()
    return _json({"ok": res["ok"], "result": res, "nour": await NH.status_view()}, 200 if res["ok"] else 400)


def make_nour_report(bot: Bot):
    async def nour_report(request: web.Request) -> web.Response:
        _auth(request)
        from app.services import nour_health as NH
        try:
            text = await NH.daily_report(bot, force=True)
        except Exception as e:  # noqa: BLE001
            log.exception("nour report failed: %s", e)
            return _json({"error": "server", "message": f"فشل إعداد التقرير: {str(e)[:120]}"}, 500)
        return _json({"ok": True, "text": text, "nour": await NH.status_view()})
    return nour_report


async def reset_preview(request: web.Request) -> web.Response:
    _auth(request)
    from app.services import launch_reset as LR
    return _json(await LR.preview())


def make_reset_execute(bot: Bot):
    async def reset_execute(request: web.Request) -> web.Response:
        user = _auth(request)
        body = await _body(request)
        from app.services import launch_reset as LR
        try:
            res = await LR.execute(user["id"], str(body.get("confirm", "")), wipe_partner=bool(body.get("wipe_partner", True)))
        except ValueError as e:
            return _json({"error": "invalid", "message": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception("launch reset failed: %s", e)
            return _json({"error": "server", "message": f"فشل التصفير — لم يتغير شيء: {str(e)[:120]}"}, 500)
        from app.services import pricing as P
        await P.refresh(); await CP.refresh_runtime()
        try:
            from app.services import channels as CH
            b = res["before"]
            await CH.alert(bot, f"🧨 <b>تصفير ما قبل الانطلاق</b> نفّذه <code>{user['id']}</code>\n"
                                f"مُسح: {b.get('users', 0)} مستخدم · {b.get('orders', 0)} طلب · {b.get('topups', 0)} شحنة"
                                f"{' · ' + str(b.get('partner_channels', 0)) + ' قناة شريكة' if res['wipe_partner'] else ''}\n"
                                f"العدّادات عادت إلى 1 — أول طلب حقيقي سيكون <b>#ORD-1</b>. الإعدادات والأسعار كما هي ✅")
        except Exception:  # noqa: BLE001
            pass
        return _json({"ok": True, **res, "snapshot": await CP.snapshot()})
    return reset_execute


def setup_cpanel(app: web.Application, bot: Bot) -> None:
    app.router.add_post("/cpanel/api/nour/test", nour_test)
    app.router.add_post("/cpanel/api/nour/report", make_nour_report(bot))
    app.router.add_post("/cpanel/api/reset/preview", reset_preview)
    app.router.add_post("/cpanel/api/reset/execute", make_reset_execute(bot))
    app.router.add_post("/cpanel/api/partner/save", partner_save)
    app.router.add_post("/cpanel/api/partner/toggle", partner_toggle)
    app.router.add_post("/cpanel/api/partner/delete", partner_delete)
    app.router.add_get("/cpanel", page)
    app.router.add_post("/cpanel/api/snapshot", snapshot)
    app.router.add_post("/cpanel/api/stats", stats)
    app.router.add_post("/cpanel/api/save/{section}", save)
    app.router.add_post("/cpanel/api/channel_test", make_channel_test(bot))
