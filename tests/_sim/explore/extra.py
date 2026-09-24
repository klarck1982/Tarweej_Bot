"""اختبارات إضافية: أمان Cpanel + تحقق مدخلاته، البث مع أسماء/نصوص HTML، التسليم المجدول الفعلي."""
from __future__ import annotations

import asyncio, copy, hashlib, hmac, json, time
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from harness import *  # noqa
from crawler import seed
from scenarios import drive, bal, qv, expect, OUT

D = Decimal


def init_data(uid, age=0, token="123456:TESTTOKEN", tamper=False):
    params = {"auth_date": str(int(time.time()) - age), "query_id": "AAH",
              "user": json.dumps({"id": uid, "first_name": "x"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        params["user"] = json.dumps({"id": A, "first_name": "x"})
    return urlencode({**params, "hash": h})


async def cpanel_tests(env):
    ISSUES.role = "cpanel"
    from app.web import make_app
    from app.web_cpanel import setup_cpanel
    app = make_app(); setup_cpanel(app, env.bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18123).start()
    base = "http://127.0.0.1:18123"
    routes = sorted({r.resource.canonical for r in app.router.routes() if r.method == "POST" and "/cpanel/api/" in r.resource.canonical})
    async with aiohttp.ClientSession() as http:
        async def post(path, body=None, hdr=None):
            async with http.post(base + path, json=body or {}, headers=hdr or {}) as r:
                try:
                    j = await r.json(content_type=None)
                except Exception:  # noqa: BLE001
                    j = await r.text()
                return r.status, j
        cases = {"بلا توقيع": {}, "توقيع مزوّر": {"X-Telegram-Init-Data": init_data(A, token="1:WRONG")},
                 "عميل عادي موقّع": {"X-Telegram-Init-Data": init_data(U)},
                 "توقيع منتهي (2 ساعة)": {"X-Telegram-Init-Data": init_data(A, age=7200)},
                 "توقيع عميل مع تبديل الهوية لأدمن": {"X-Telegram-Init-Data": init_data(U, tamper=True)}}
        for label, hdr in cases.items():
            for path in routes:
                if "{" in path:
                    path = path.replace("{section}", "pricing")
                ISSUES.context = f"cpanel {label} → {path}"
                st, j = await post(path, {}, hdr)
                if st < 400:
                    ISSUES.add("critical", "cpanel-auth-bypass", f"{label}: {path} أعاد {st}")
        ok = {"X-Telegram-Init-Data": init_data(A)}
        ISSUES.context = "cpanel admin"
        st, snap = await post("/cpanel/api/snapshot", {}, ok)
        print("    snapshot:", st, list(snap)[:12] if isinstance(snap, dict) else snap)
        pricing = (snap or {}).get("pricing") if isinstance(snap, dict) else None
        if isinstance(pricing, dict):
            print("    pricing keys:", list(pricing)[:20])
            for key in list(pricing):
                v = pricing[key]
                if isinstance(v, (int, float, str)) and str(v).replace(".", "", 1).isdigit():
                    for bad in (-1, 0, "abc", 1e9):
                        body = copy.deepcopy(pricing); body[key] = bad
                        ISSUES.context = f"cpanel pricing.{key}={bad!r}"
                        st, j = await post("/cpanel/api/save/pricing", body, ok)
                        if st == 200:
                            ISSUES.add("high" if bad in (-1, 0) else "medium", "cpanel-pricing-accepted",
                                       f"Cpanel قبل pricing.{key} = {bad!r}")
                            await post("/cpanel/api/save/pricing", pricing, ok)
                        elif st >= 500:
                            ISSUES.add("medium", "cpanel-500", f"pricing.{key}={bad!r} → {st} {str(j)[:120]}")
            # أسعار متداخلة: إضافات التصميم وباقاتها
            for path_desc, mutate in (
                ("addons.copy.price=0", lambda b: b["addons"]["copy"].__setitem__("price", "0")),
                ("addon_bundles[0].price=0", lambda b: b["addon_bundles"][0].__setitem__("price", "0")),
                ("addon_bundles[0].was<price", lambda b: b["addon_bundles"][0].__setitem__("was", "1")),
            ):
                body = copy.deepcopy(pricing)
                try:
                    mutate(body)
                except (KeyError, IndexError, TypeError):
                    continue
                ISSUES.context = f"cpanel pricing.{path_desc}"
                st, j = await post("/cpanel/api/save/pricing", body, ok)
                if st == 200:
                    ISSUES.add("high", "cpanel-pricing-accepted", f"Cpanel قبل {path_desc}")
                    await post("/cpanel/api/save/pricing", pricing, ok)
        # عناوين محافظ خاطئة من Cpanel
        for code, bad_addr in (("usdt_trc20", "0x1234567890abcdef1234567890abcdef12345678"),
                               ("usdt_bep20", "TXYZ1234567890abcdefghijkmnopqrstu"),
                               ("usdt_trc20", "hello-this-is-not-an-address-at-all")):
            ISSUES.context = f"cpanel payments {code}={bad_addr[:12]}…"
            st, j = await post("/cpanel/api/save/payments", {code: {"address": bad_addr}}, ok)
            if st == 200:
                ISSUES.add("high", "cpanel-wallet-accepted", f"Cpanel قبل عنوان {bad_addr[:16]}… لشبكة {code}")
            elif st >= 500:
                ISSUES.add("medium", "cpanel-500", f"payments {code} → {st}")
        st, j = await post("/cpanel/api/save/payments", {"usdt_bep20": {"address": "0x" + "ab" * 20}}, ok)
        if st != 200:
            ISSUES.add("high", "cpanel-wallet-rejected-valid", f"Cpanel رفض عنوان BEP20 صحيحاً: {st} {str(j)[:100]}")
        # باقة مجدولة بقيم خاطئة
        for bad in ({"code": "x", "title": "t", "price_usd": "5", "total_items": 1, "duration_days": 1},
                    {"code": "ok_pkg", "title": "باقة", "price_usd": "-5", "total_items": 3, "duration_days": 3},
                    {"code": "ok_pkg2", "title": "<b>باقة", "price_usd": "5", "total_items": 3, "duration_days": 3, "send_time": "25:99"}):
            ISSUES.context = f"cpanel scheduled package {bad}"
            st, j = await post("/cpanel/api/scheduled/package/save", bad, ok)
            if st >= 500:
                ISSUES.add("medium", "cpanel-500", f"حفظ باقة {bad} → {st}: {str(j)[:150]}")
            elif st == 200:
                ISSUES.add("medium", "cpanel-package-accepted", f"قُبلت باقة غير صالحة: {bad}")
        # قناة شريكة بـ HTML في الاسم ثم عرضها للعميل
        ISSUES.context = "cpanel partner html"
        st, j = await post("/cpanel/api/partner/save", {"title": "قناة <b>X & Y", "url": "@xy_channel", "category": "news",
                                                          "subscribers": 1000, "price_24h": "5", "blurb": "وصف <i>مفتوح"}, ok)
        print("    partner with html:", st)
        u = env.actor(U, "cpanel-html-view")
        await drive(u, ["t:/start", "cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all"])
        cid = await qv("SELECT max(id) FROM partner_channels")
        await drive(u, [f"cb:tgp:ch:{cid}"])
        # إعادة الضبط تحتاج تأكيداً؟
        ISSUES.context = "cpanel reset"
        st, j = await post("/cpanel/api/reset/execute", {}, ok)
        print("    reset/execute بلا تأكيد:", st, str(j)[:150])
        if st == 200 and await qv("SELECT count(*) FROM users") == 0:
            ISSUES.add("high", "reset-no-confirm", "reset/execute مسح كل شيء بلا عبارة تأكيد")
    await runner.cleanup()


async def broadcast_html(env):
    from app.bot.handlers.admin import tools as TT
    a = env.actor(A, "broadcast")
    await drive(a, ["t:/admin", "cb:adm:bc", "t:عرض خاص <3 للجميع & خصم {name}", "cb:adm:bc:no_photo", "cb:adm:bc:aud:all"])
    start = len(sent)
    cd = "adm:bc:confirm2" if a.find_cb("adm:bc:confirm2") else "adm:bc:confirm"
    # ضغطتا تأكيد متزامنتان: يجب أن يُرسل البث مرة واحدة فقط
    await asyncio.gather(a.click(cd, label="تأكيد البث 1"), a.click(cd, label="تأكيد البث 2 متزامن"))
    if TT.BROADCAST_TASKS:
        await asyncio.wait_for(asyncio.gather(*list(TT.BROADCAST_TASKS)), 60)   # البث يعمل في الخلفية
    msgs = [d for n, d in sent[start:] if n in ("SendMessage", "SendPhoto") and str(d.get("chat_id")) != str(A)]
    per_user = {}
    for d in msgs:
        per_user[str(d.get("chat_id"))] = per_user.get(str(d.get("chat_id")), 0) + 1
    dup = {k: v for k, v in per_user.items() if v > 1}
    print(f"    broadcast recipients={len(per_user)} messages={len(msgs)} duplicates={dup}")
    if dup:
        ISSUES.add("critical", "broadcast-duplicate", f"مستخدمون استلموا البث أكثر من مرة: {dup}")
    if msgs and "&lt;3" not in (msgs[0].get("text") or ""):
        ISSUES.add("high", "broadcast-not-escaped", f"نص البث غير مهرّب: {(msgs[0].get('text') or '')[:80]!r}")
    rep = [d.get("text") for n, d in sent[start:] if n == "SendMessage" and str(d.get("chat_id")) == str(A)]
    print("    broadcast report:", [re.sub(r"\s+", " ", (t or ""))[:160] for t in rep[-1:]])
    print("    sample:", [(m.get("text") or "")[:80] for m in msgs[:2]])


async def scheduled_delivery(env):
    from app.services import scheduled as S, scheduler
    from datetime import date, timedelta, datetime, timezone
    u, a = env.actor(U, "sched-user"), env.actor(A, "sched-admin")
    await drive(u, ["t:/start", "cb:sub:list", "cb:sub:pkg:daily7", "cb:sub:buy:daily7"])
    await drive(u, ["cb:" + u.find_cb("sub:confirm:")])
    sid = await qv("SELECT max(id) FROM scheduled_subscriptions WHERE user_id=$1", U)
    for seq in range(1, 8):
        await drive(a, [f"cb:adm:sub:{sid}:add"])
        if await a.state() == "ScheduledAdmin:seq":
            await a.text(str(seq))
        if await a.state() == "ScheduledAdmin:file":
            await a.photo()
        if await a.state() == "ScheduledAdmin:copy":
            await a.text(f"نص اليوم {seq} — <b>عرض</b> & خصم")
    items = await qv("SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=$1", sid)
    print("    items added:", items)
    try:
        await S.activate(sid, (date.today() - timedelta(days=3)).isoformat(), "00:05")
    except Exception as e:  # noqa: BLE001
        ISSUES.add("high", "sched-activate", f"{type(e).__name__}: {e}")
        return
    ISSUES.context = "scheduler tick"
    start = len(sent)
    try:
        await scheduler._scheduled_designs(env.bot)
    except Exception as e:  # noqa: BLE001
        ISSUES.add("high", "scheduler-crash", f"{type(e).__name__}: {e}")
    got = [(n, (d.get("caption") or d.get("text") or "")[:60]) for n, d in sent[start:] if str(d.get("chat_id")) == str(U)]
    row = await qv("SELECT sent_count FROM scheduled_subscriptions WHERE id=$1", sid)
    print(f"    tick1 → sent to user: {len(got)} | sent_count={row}")
    # tick ثانٍ فوراً: يجب ألا يعيد إرسال نفس العناصر
    start = len(sent)
    await scheduler._scheduled_designs(env.bot)
    again = [n for n, d in sent[start:] if str(d.get("chat_id")) == str(U)]
    row2 = await qv("SELECT sent_count FROM scheduled_subscriptions WHERE id=$1", sid)
    print(f"    tick2 → re-sent: {len(again)} | sent_count={row2}")
    past_due = await qv("SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=$1 AND scheduled_at < now()", sid)
    if len(got) + len(again) and row2 and past_due and row2 > past_due:
        ISSUES.add("high", "sched-resend", f"أرسل {row2} عناصر بينما المستحق {past_due}")
    # v0.9.2: بعد التأخر يُرسل تسليم واحد لكل مشترك في الساعة — لا دفعة
    if past_due >= 2 and (row2 or 0) > 1:
        ISSUES.add("medium", "sched-burst", f"أُرسلت {row2} تصاميم متأخرة دفعة واحدة (المستحق {past_due})")
    late_note = [d for n, d in sent if str(d.get("chat_id")) == str(A) and "تسليمات متأخرة" in (d.get("text") or "")]
    print(f"    admin late notice: {len(late_note)}")
    if past_due >= 2 and not late_note:
        ISSUES.add("low", "sched-late-silent", "لم يُبلَّغ الأدمن بالتسليمات المتأخرة")
    # مرور ساعة: التسليم التالي يُرسل
    await qv("UPDATE scheduled_subscription_items SET sent_at = now() - interval '61 minutes' WHERE subscription_id=$1 AND status='sent' RETURNING 1", sid)
    await scheduler._scheduled_designs(env.bot)
    row3 = await qv("SELECT sent_count FROM scheduled_subscriptions WHERE id=$1", sid)
    print(f"    after 1h → sent_count={row3}")
    if past_due >= 2 and row3 != (row2 or 0) + 1:
        ISSUES.add("medium", "sched-catchup", f"بعد ساعة كان المتوقع {(row2 or 0) + 1} تسليمات، الفعلي {row3}")
    # مستخدم حظر البوت أثناء الاشتراك
    BLOCKED_CHATS.add(U)
    # التسليم n يقع في (البداية + n-1 يوماً) ← نبدأ قبل 3 أيام حتى يستحق التسليم 3 الآن
    await S.reschedule(sid, (date.today() - timedelta(days=3)).isoformat(), "00:00") if hasattr(S, "reschedule") else None
    await qv("UPDATE scheduled_subscription_items SET sent_at = now() - interval '61 minutes' WHERE subscription_id=$1 AND status='sent' RETURNING 1", sid)
    before_attempts = await qv("SELECT coalesce(sum(attempts),0) FROM scheduled_subscription_items WHERE subscription_id=$1", sid)
    try:
        await scheduler._scheduled_designs(env.bot)
    except Exception as e:  # noqa: BLE001
        ISSUES.add("high", "scheduler-blocked-crash", f"{type(e).__name__}: {e}")
    BLOCKED_CHATS.discard(U)
    r = await q1_("SELECT status, sent_count, last_error FROM scheduled_subscriptions WHERE id=$1", sid)
    after_attempts = await qv("SELECT coalesce(sum(attempts),0) FROM scheduled_subscription_items WHERE subscription_id=$1", sid)
    print("    after blocked:", dict(r), "attempts:", before_attempts, "→", after_attempts)
    if after_attempts == before_attempts:
        ISSUES.add("low", "sched-blocked-untested", "لم يُحاوَل أي تسليم لمستخدم حظر البوت — الاختبار لم يُنفَّذ")
    elif after_attempts - before_attempts > 1:
        ISSUES.add("medium", "sched-retry-burst", f"{after_attempts - before_attempts} محاولات متتالية في دورة واحدة لمستخدم حاظر")
    if r["status"] != "paused":
        ISSUES.add("medium", "sched-blocked-not-paused", f"الباقة لم تتوقف بعد حظر البوت: {r['status']}")
    note = [d for n, d in sent if str(d.get("chat_id")) == str(A) and "العميل حظر البوت" in (d.get("text") or "")]
    if not note:
        ISSUES.add("low", "sched-blocked-silent", "لم يُبلَّغ الأدمن بإيقاف الباقة بسبب الحظر")


async def q1_(sql, *a):
    from app.db import pool as db
    return await db.fetchrow(sql, *a)


async def main():
    env = await Env(verbose=True, logfile=f"{OUT}/extra_log.txt").boot()
    await seed(env)
    for uid in (U, U3, U2):
        act = env.actor(uid, "warm"); await act.text("/start"); await act.click("nav:accept")
    print("══ Cpanel"); await cpanel_tests(env)
    await seed(env)
    for uid in (U, U3, U2):
        act = env.actor(uid, "warm"); await act.text("/start"); await act.click("nav:accept")
    print("══ البث"); await broadcast_html(env)
    print("══ التسليم المجدول"); await scheduled_delivery(env)
    report(f"{OUT}/extra_issues.json", "نتيجة الاختبارات الإضافية")
    await env.close()

if __name__ == "__main__":
    asyncio.run(main())
