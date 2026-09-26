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

import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile
from aiohttp import web

from app.db import pool as db
from app.services import cpanel as CP
from app.services import scheduled as SD

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


async def scheduled_snapshot(request: web.Request) -> web.Response:
    _auth(request)
    return _json(await SD.snapshot())


async def scheduled_package_save(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    try:
        package, existed = await SD.save_package(body, user["id"])
        await CP.audit(user["id"], "scheduled", f"package.{package['code']}", None if not existed else "existing", package)
    except ValueError as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        log.exception("scheduled package save failed: %s", e)
        return _json({"error": "server", "message": "تعذر حفظ الباقة."}, 500)
    return _json({"ok": True, "package": package, "scheduled": await SD.snapshot()})


async def scheduled_package_toggle(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    code = str(body.get("code") or "")
    try:
        package = await SD.toggle_package(code, bool(body.get("enabled")))
    except Exception as e:  # noqa: BLE001
        return _json({"error": "server", "message": str(e)[:160]}, 500)
    if not package:
        return _json({"error": "missing", "message": "الباقة غير موجودة"}, 404)
    await CP.audit(user["id"], "scheduled", f"package.{code}.enabled", None, package["enabled"])
    return _json({"ok": True, "package": package, "scheduled": await SD.snapshot()})


async def scheduled_package_delete(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    code = str(body.get("code") or "")
    try:
        ok = await SD.delete_package(code)
    except Exception as e:  # noqa: BLE001
        return _json({"error": "server", "message": str(e)[:160]}, 500)
    if not ok:
        return _json({"error": "missing", "message": "الباقة غير موجودة"}, 404)
    await CP.audit(user["id"], "scheduled", f"package.{code}.deleted", True, False)
    return _json({"ok": True, "scheduled": await SD.snapshot()})


async def scheduled_list(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    tab = str(body.get("tab") or "active")
    if tab not in ("active", "paused", "done"):
        tab = "active"
    q = str(body.get("q") or "")[:100]
    rows = await SD.list_views(q, limit=200)
    groups = {
        "active": {"scheduled", "awaiting_assets"},
        "paused": {"paused"},
        "done": {"completed", "cancelled", "refunded"},
    }
    wanted = groups[tab]
    return _json({"items": [r for r in rows if str(r.get("status")) in wanted], "tab": tab, "count": len(rows)})


async def scheduled_detail(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    try:
        item = await SD.detail(int(body.get("id") or 0))
    except (TypeError, ValueError):
        item = None
    if not item:
        return _json({"error": "missing", "message": "الاشتراك غير موجود"}, 404)
    return _json(item)


async def scheduled_create(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    bot = request.app["bot"]
    try:
        sub = await SD.create_manual(
            admin_id=user["id"],
            name=str(body.get("user") or "").strip(),
            target_ref=str(body.get("target_ref") or "").strip(),
            total_items=max(1, int(body.get("total_items") or 0)),
            send_time=str(body.get("send_time") or "20:00"),
            package_title=str(body.get("package_title") or "").strip(),
            note=str(body.get("note") or "").strip()[:200],
            price_ref=str(body.get("price_ref") or "").strip(),
            bot=bot,
        )
    except ValueError as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        log.exception("scheduled create failed: %s", e)
        return _json({"error": "server", "message": "تعذر إنشاء الاشتراك."}, 500)
    await CP.audit(user["id"], "scheduled", f"subscription.{sub['id']}.create", None, sub.get("status"))
    return _json({"ok": True, "subscription": await SD.detail(int(sub["id"]))})


async def scheduled_update(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    bot = request.app["bot"]
    sid = int(body.get("id") or 0)
    kwargs: dict[str, object] = {}
    if body.get("send_time") is not None:
        kwargs["send_time"] = str(body.get("send_time"))
    if body.get("user") is not None:
        kwargs["name"] = str(body.get("user"))
    if body.get("package_title") is not None:
        kwargs["package_title"] = str(body.get("package_title"))
    if body.get("note") is not None:
        kwargs["note"] = str(body.get("note"))
    if body.get("total_items") is not None:
        kwargs["total_items"] = int(body.get("total_items"))
    try:
        await SD.update_sub(sid, **kwargs)
    except (TypeError, ValueError) as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    if body.get("target_ref") is not None:
        raw = str(body.get("target_ref")).strip()
        try:
            target = await SD.resolve_target(bot, raw) if raw else None
        except ValueError as e:
            return _json({"error": "invalid", "message": str(e)}, 400)
        from app.db.repo import scheduled as SR
        if target:
            await SR.update_fields(sid, target_chat_id=target["chat_id"],
                                   target_kind=target["kind"], target_title=target["title"])
        else:
            await SR.update_fields(sid, target_chat_id=None, target_kind=None, target_title=None)
    await CP.audit(user["id"], "scheduled", f"subscription.{sid}.update", None, str(kwargs))
    return _json({"ok": True, "subscription": await SD.detail(sid)})


async def scheduled_action(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    sid = int(body.get("id") or 0)
    action = str(body.get("action") or "")
    bot = request.app["bot"]
    try:
        if action == "pause":
            sub = await SD.pause(sid)
        elif action == "resume":
            sub = await SD.resume(sid)
        elif action == "activate":
            sub = await SD.activate(sid)
        elif action == "send_now":
            status, _info = await SD.deliver_next(bot, sid, manual=True)
            await CP.audit(user["id"], "scheduled", f"subscription.{sid}.send_now", None, status)
            return _json({"ok": True, "delivery": status, "subscription": await SD.detail(sid)})
        elif action == "extend":
            add = max(1, int(body.get("add_count") or 0))
            sub = await SD.extend(sid, add, 0)
        elif action == "delete_msg":
            seq = int(body.get("seq") or 0)
            ok, err = await SD.delete_sent_message(bot, sid, seq)
            if not ok:
                return _json({"error": "invalid", "message": err or "تعذر حذف الرسالة"}, 400)
            await CP.audit(user["id"], "scheduled", f"pair.{sid}.{seq}.delete_msg", None, "deleted")
            return _json({"ok": True, "subscription": await SD.detail(sid)})
        elif action == "cancel":
            sub, refund = await SD.cancel(sid, admin_id=user["id"])
            if not sub:
                return _json({"error": "missing", "message": "الاشتراك غير موجود"}, 404)
            await CP.audit(user["id"], "scheduled", f"subscription.{sid}.cancel", None, str(refund))
            try:
                await bot.send_message(sub["user_id"],
                    f"↩️ ألغيت الإدارة باقة التصميم «{SD._esc(sub.get('package_title'))}»"
                    + (f" واستُرد لك ما قيمته {refund} رصيد من الأيام غير المنفذة." if refund > 0 else "."))
            except Exception:  # noqa: BLE001
                pass
            return _json({"ok": True, "refund": str(refund), "subscription": await SD.detail(sid)})
        else:
            return _json({"error": "invalid", "message": "الإجراء غير معروف"}, 400)
    except ValueError as e:
        return _json({"error": "invalid", "message": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        log.exception("scheduled action failed: %s", e)
        return _json({"error": "server", "message": str(e)[:160]}, 500)
    if not sub:
        return _json({"error": "missing", "message": "الاشتراك غير موجود"}, 404)
    await CP.audit(user["id"], "scheduled", f"subscription.{sid}.{action}", None, sub.get("status"))
    try:
        if action == "pause":
            await bot.send_message(sub.get("user_id"), "⏸️ تم إيقاف باقة التصميم مؤقتاً.")
        elif action == "resume":
            await bot.send_message(sub.get("user_id"), "▶️ استؤنفت باقة التصميم.")
        elif action == "extend":
            await bot.send_message(sub.get("user_id"),
                f"➕ مُدّدت باقة التصميم «{SD._esc(sub.get('package_title'))}» · المجموع الآن {sub['total_items']} تصميماً.")
    except Exception:  # noqa: BLE001
        pass
    return _json({"ok": True, "subscription": await SD.detail(sid)})


async def scheduled_pair_text(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    item, err = await SD.save_pair(int(body.get("id") or 0), int(body.get("seq") or 0),
                                   copy=str(body.get("copy") or ""))
    if err == "الاشتراك غير موجود":
        return _json({"error": "missing", "message": err}, 404)
    if err:
        return _json({"error": "invalid", "message": err}, 400)
    await CP.audit(user["id"], "scheduled", f"pair.{body.get('id')}.{body.get('seq')}.text", None, "saved")
    return _json({"ok": True, "item": item})


async def scheduled_pair_delete(request: web.Request) -> web.Response:
    user = _auth(request)
    body = await _body(request)
    sid, seq = int(body.get("id") or 0), int(body.get("seq") or 0)
    ok = await SD.delete_pair(sid, seq)
    if not ok:
        return _json({"error": "missing", "message": "لا يمكن حذف هذا الزوج (غير موجود أو أُرسل)"}, 404)
    await CP.audit(user["id"], "scheduled", f"pair.{sid}.{seq}.delete", None, "deleted")
    return _json({"ok": True})


def make_scheduled_pair_upload(bot: Bot):
    async def scheduled_pair_upload(request: web.Request) -> web.Response:
        user = _auth(request)
        if request.content_length and request.content_length > 22 * 1024 * 1024:
            return _json({"error": "too_large", "message": "الملف أكبر من 20MB."}, 413)
        try:
            reader = await request.multipart()
            fields: dict[str, str] = {}
            data = None
            filename = "design.bin"
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "file":
                    filename = part.filename or filename
                    data = await part.read(decode=False)
                    if len(data) > 20 * 1024 * 1024:
                        return _json({"error": "too_large", "message": "الملف أكبر من 20MB."}, 413)
                else:
                    fields[part.name] = (await part.text()).strip()
            if not data:
                return _json({"error": "invalid", "message": "أرفق ملف التصميم أولاً."}, 400)
            sid = int(fields.get("id") or 0)
            seq = int(fields.get("seq") or 0)
            # فشل سريع قبل رحلة تيليغرام: اشتراك موجود؟ تسلسل صالح؟ الزوج غير مُسلَّم؟
            sub = await SD.detail(sid)
            if not sub:
                return _json({"error": "missing", "message": "الاشتراك غير موجود"}, 404)
            if not 1 <= seq <= 365:
                return _json({"error": "invalid", "message": "رقم التسلسل خارج الحدود"}, 400)
            existing = next((i for i in (sub.get("items") or []) if int(i.get("seq") or 0) == seq), None)
            if existing and existing.get("status") == "sent":
                return _json({"error": "invalid", "message": "هذا الزوج سُلّم بالفعل — لا يُعدَّل"}, 400)
            # النوع المحفوظ يطابق طريقة الرفع تماماً: file_id الصورة لا يعمل مع
            # send_document والعكس (هذا الخلل كان سبب «خطأ في الإرسال» للأزواج المرفوعة من Cpanel).
            is_photo = str(filename).lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            up_caption = f"📦 رفع زوج تصميم SUB-{sid} · تسلسل {seq}"
            # الرفع إلى تيليغرام (للحصول على file_id) — كل فشل هنا بسبب واضح وقابل للعلاج
            try:
                if is_photo:
                    msg = await bot.send_photo(user["id"], BufferedInputFile(data, filename=filename),
                                               caption=up_caption)
                    file_id = msg.photo[-1].file_id if msg.photo else None
                    kind = "photo"
                else:
                    msg = await bot.send_document(user["id"], BufferedInputFile(data, filename=filename),
                                                  caption=up_caption)
                    file_id = (msg.document.file_id if msg.document else None) or \
                              (msg.photo[-1].file_id if msg.photo else None)
                    kind = "document" if msg.document else "photo"
                if not file_id:
                    return _json({"error": "server",
                                  "message": "تيليغرام لم يرجع معرّفاً للملف — أعد المحاولة."}, 502)
            except TelegramForbiddenError:
                return _json({"error": "no_dm",
                              "message": "تعذّر الإرسال إلى خاصّك — افتح محادثة البوت واضغط Start ثم أعد الرفع."}, 400)
            except TelegramRetryAfter as e:
                return _json({"error": "retry",
                              "message": f"تيليغرام طلب الانتظار {e.retry_after} ثانية — أعد المحاولة بعدها."}, 429)
            except TelegramNetworkError:
                return _json({"error": "network",
                              "message": "تعذّر الاتصال بخوادم تيليغرام — أعد المحاولة بعد قليل."}, 502)
            except TelegramBadRequest as e:
                return _json({"error": "rejected",
                              "message": f"تيليغرام رفض الملف ({e.message}) — جرّب صيغة أو حجماً آخر."}, 400)
            try:
                await bot.delete_message(user["id"], msg.message_id)
            except Exception:  # noqa: BLE001
                pass
            copy = fields.get("copy") or fields.get("copy_text") or None
            item, err = await SD.save_pair(sid, seq, copy=copy, photo=(kind, file_id))
            if err:
                return _json({"error": "invalid", "message": err}, 400 if err != "الاشتراك غير موجود" else 404)
            await CP.audit(user["id"], "scheduled", f"pair.{sid}.{seq}.upload", None, filename)
            return _json({"ok": True, "item": item})
        except ValueError as e:
            return _json({"error": "invalid", "message": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception("scheduled pair upload failed: %s", e)
            detail = f"{type(e).__name__}: {e}".strip()
            return _json({"error": "server", "message": f"تعذر الرفع ({detail})"}, 500)
    return scheduled_pair_upload


async def scheduled_photo(request: web.Request) -> web.Response:
    try:
        _auth(request)
    except web.HTTPException:
        token = str(request.rel_url.query.get("token") or "")
        secret = request.app.get("panel_secret") or ""
        if not token or not secret or not hmac.compare_digest(token, secret):
            raise
    sid = int(request.rel_url.query.get("sub") or 0)
    seq = int(request.rel_url.query.get("seq") or 0)
    data, filename, content_type = await SD.pair_photo_bytes(request.app["bot"], sid, seq)
    if data is None:
        return web.Response(status=404, text="not found")
    return web.Response(body=data, headers={
        "Content-Type": content_type or "application/octet-stream",
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": "private, max-age=3600",
    })


async def financial_ledger(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    q = str(body.get("q") or "").strip().lstrip("@")[:100].lower()
    kind = str(body.get("type") or "").strip()
    if kind not in ("", "topup", "order_charge", "refund", "referral", "adjustment"):
        kind = ""
    period = str(body.get("period") or "7d")
    days = {"today": 1, "7d": 7, "30d": 30, "all": None}.get(period, 7)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    page = max(1, int(body.get("page") or 1))
    limit = 50
    offset = (page - 1) * limit
    where = [
        "($1 = '' OR lower(coalesce(u.name,'')) LIKE '%'||$1||'%' OR lower(coalesce(u.username,'')) LIKE '%'||$1||'%' OR l.user_id::text=$1 OR coalesce(l.ref_id::text,'')=$1)",
        "($2 = '' OR l.type = $2)",
        "($3::timestamptz IS NULL OR l.created_at >= $3)",
    ]
    args = [q, kind, since]
    rows = await db.fetch(
        """
        SELECT l.id, l.user_id, l.type, l.amount_usd, l.ref_type, l.ref_id,
               l.note, l.admin_id, l.created_at,
               u.name AS user_name, u.username AS user_username
        FROM ledger l JOIN users u ON u.tg_id = l.user_id
        WHERE """ + " AND ".join(where) + " ORDER BY l.id DESC LIMIT $4 OFFSET $5",
        *args, limit, offset,
    )
    count = await db.fetchval(
        "SELECT count(*) FROM ledger l JOIN users u ON u.tg_id=l.user_id WHERE " + " AND ".join(where), *args
    )
    totals = await db.fetchrow(
        """
        SELECT coalesce(sum(CASE WHEN l.amount_usd > 0 THEN l.amount_usd ELSE 0 END),0) AS incoming,
               coalesce(sum(CASE WHEN l.amount_usd < 0 THEN -l.amount_usd ELSE 0 END),0) AS outgoing,
               coalesce(sum(l.amount_usd),0) AS net
        FROM ledger l JOIN users u ON u.tg_id=l.user_id
        WHERE """ + " AND ".join(where), *args,
    )
    pending = await db.fetchval("SELECT count(*) FROM topups WHERE status='pending'") or 0
    return _json({
        "items": [dict(r) for r in rows], "count": int(count or 0), "page": page,
        "pages": max(1, (int(count or 0) + limit - 1) // limit), "period": period,
        "incoming": totals["incoming"], "outgoing": totals["outgoing"], "net": totals["net"],
        "pending_topups": int(pending),
    })


async def users_ledger(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    try:
        uid = int(body.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return _json({"error": "invalid", "message": "معرف المستخدم غير صالح"}, 400)
    user = await db.fetchrow("SELECT tg_id, name, username, balance_usd FROM users WHERE tg_id=$1", uid)
    if not user:
        return _json({"error": "missing", "message": "المستخدم غير موجود"}, 404)
    rows = await db.fetch(
        "SELECT id, type, amount_usd, ref_type, ref_id, note, admin_id, created_at "
        "FROM ledger WHERE user_id=$1 ORDER BY id DESC LIMIT 100", uid,
    )
    return _json({"user": dict(user), "rows": [dict(r) for r in rows]})


async def users_directory(request: web.Request) -> web.Response:
    _auth(request)
    body = await _body(request)
    q = str(body.get("q") or "").strip().lstrip("@")[:100].lower()
    page = max(1, int(body.get("page") or 1))
    limit = 50
    rows = await db.fetch(
        """
        SELECT u.tg_id, u.name, u.username, u.balance_usd, u.created_at, u.last_seen,
               u.accepted_terms_at, u.is_blocked, u.is_blocked_bot,
               (SELECT count(*) FROM orders o WHERE o.user_id=u.tg_id) AS orders_count,
               (SELECT count(*) FROM topups t WHERE t.user_id=u.tg_id AND t.status='approved') AS topups_count
        FROM users u
        WHERE ($1='' OR lower(coalesce(u.name,'')) LIKE '%'||$1||'%' OR lower(coalesce(u.username,'')) LIKE '%'||$1||'%' OR u.tg_id::text=$1)
        ORDER BY u.last_seen DESC NULLS LAST
        LIMIT $2 OFFSET $3
        """, q, limit, (page - 1) * limit,
    )
    total = await db.fetchval(
        "SELECT count(*) FROM users u WHERE ($1='' OR lower(coalesce(u.name,'')) LIKE '%'||$1||'%' OR lower(coalesce(u.username,'')) LIKE '%'||$1||'%' OR u.tg_id::text=$1)", q
    )
    return _json({"items": [dict(r) for r in rows], "count": int(total or 0), "page": page})


def setup_cpanel(app: web.Application, bot: Bot) -> None:
    app["bot"] = bot
    app["panel_secret"] = secrets.token_urlsafe(24)
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
    app.router.add_post("/cpanel/api/scheduled/snapshot", scheduled_snapshot)
    app.router.add_post("/cpanel/api/scheduled/list", scheduled_list)
    app.router.add_post("/cpanel/api/scheduled/detail", scheduled_detail)
    app.router.add_post("/cpanel/api/scheduled/create", scheduled_create)
    app.router.add_post("/cpanel/api/scheduled/update", scheduled_update)
    app.router.add_post("/cpanel/api/scheduled/action", scheduled_action)
    app.router.add_post("/cpanel/api/scheduled/pair/text", scheduled_pair_text)
    app.router.add_post("/cpanel/api/scheduled/pair/delete", scheduled_pair_delete)
    app.router.add_post("/cpanel/api/scheduled/pair/upload", make_scheduled_pair_upload(bot))
    app.router.add_get("/cpanel/api/scheduled/photo", scheduled_photo)
    app.router.add_post("/cpanel/api/ledger", financial_ledger)
    app.router.add_post("/cpanel/api/users/directory", users_directory)
    app.router.add_post("/cpanel/api/users/ledger", users_ledger)
    app.router.add_post("/cpanel/api/channel_test", make_channel_test(bot))
