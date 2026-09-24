"""سيناريوهات عميقة كاملة (عميل ↔ أدمن) مع تحقق من القاعدة بعد كل مرحلة.

تشغيل:  /tmp/freshdb.sh && python scenarios.py [اسم_سيناريو ...]
"""
from __future__ import annotations

import asyncio, os, re, sys
from decimal import Decimal

from harness import *  # noqa
from crawler import seed  # noqa

D = Decimal
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"[\u2800\u200b]", "", t or "")).strip()


async def drive(a: Actor, steps: list[str], show=False):
    for s in steps:
        if s.startswith("cb:"):
            await a.click(s[3:])
        elif s.startswith("p:"):
            await a.press(s[2:])
        elif s.startswith("t:"):
            await a.text(s[2:])
        elif s == "photo":
            await a.photo()
        elif s == "doc":
            await a.document("design_final.png", "image/png")
        elif s == "video":
            await a.video()
        else:
            raise ValueError(s)
        if show:
            print(f"    {a.role} {s[:40]:40} → st={await a.state()} | {clean(a.last_texts[-1] if a.last_texts else '')[:110]}")


def expect(cond, sev, kind, detail):
    if not cond:
        ISSUES.add(sev, kind, detail)
    return cond


async def bal(uid):
    from app.db.repo import users
    return await users.get_balance(uid)


async def q1(sql, *a):
    from app.db import pool as db
    return await db.fetchrow(sql, *a)


async def qv(sql, *a):
    from app.db import pool as db
    return await db.fetchval(sql, *a)


# ─────────────────────────── S1 الشحن ───────────────────────────
async def s_topup(env, show):
    u, a = env.actor(U2, "S1-user"), env.actor(A, "S1-admin")
    await drive(u, ["t:/start", "cb:nav:accept"])
    # مبالغ حدّية
    await drive(u, ["cb:bal:topup", "cb:bal:m:usdt_trc20", "cb:bal:amt:type"], show)
    for bad in ["4.99", "0", "-10", "abc", "1e5", "100001", "5.555"]:
        await u.text(bad)
        st = await u.state()
        if st != "Topup:amount":
            ISSUES.add("medium", "topup-amount-accepted", f"مبلغ الشحن {bad!r} قُبل (الحالة الآن {st})")
            await u.text("/cancel"); await drive(u, ["cb:bal:topup", "cb:bal:m:usdt_trc20", "cb:bal:amt:type"])
    await drive(u, ["t:25"], show)
    tid = await qv("SELECT max(id) FROM topups WHERE user_id=$1", U2)
    await drive(u, [f"cb:bal:paid:{tid}", "t:abc", "photo"], show)
    row = await q1("SELECT status, amount_usd FROM topups WHERE id=$1", tid)
    print("    topup#1:", dict(row))
    # الأدمن يعتمد
    await drive(a, [f"cb:adm:top:{tid}:ok"], show)
    expect(await bal(U2) == D("25"), "critical", "topup-approve", f"الرصيد بعد اعتماد 25$ = {await bal(U2)}")
    # ضغطة اعتماد ثانية (زر قديم) — يجب ألا تضاعف الرصيد
    await a.click(f"adm:top:{tid}:ok", label="🔁 اعتماد مكرر", expect_ok=False)
    expect(await bal(U2) == D("25"), "critical", "double-approve", f"اعتماد مكرر ضاعف الرصيد: {await bal(U2)}")
    # رفض بعد الاعتماد؟
    await a.click(f"adm:top:{tid}:no:unclear", label="رفض بعد اعتماد", expect_ok=False)
    expect(await bal(U2) == D("25"), "critical", "reject-after-approve", f"الرفض بعد الاعتماد غيّر الرصيد: {await bal(U2)}")

    # شحنة 2: TxID نصي + تعديل المبلغ بقيم حدّية
    await drive(u, ["cb:bal:topup", "cb:bal:m:usdt_bep20", "cb:bal:amt:10"], show)
    tid2 = await qv("SELECT max(id) FROM topups WHERE user_id=$1", U2)
    await drive(u, [f"cb:bal:paid:{tid2}", "t:0xabc123def4567890abc123def4567890abc123def4567890abc123def4567890"], show)
    await drive(a, [f"cb:adm:top:{tid2}:adj"], show)
    for bad in ["0", "-5", "abc", "1000000"]:
        await a.text(bad)
        if await a.state() != "AdminTopup:adjust_amount":
            ISSUES.add("high" if bad in ("0", "-5") else "medium", "adjust-accepted", f"تعديل مبلغ الشحن قبل {bad!r}")
            break
    if await a.state() == "AdminTopup:adjust_amount":
        # فرق كبير (30 بدل 10) ← يجب أن يطلب تأكيداً صريحاً ولا يعتمد فوراً
        b_before = await bal(U2)
        await drive(a, ["t:30"], show)
        expect(await a.state() == "AdminTopup:adjust_confirm" and await bal(U2) == b_before, "high", "adjust-no-confirm",
               f"مبلغ 30 بدل 10 اعتُمد بلا تأكيد (state={await a.state()}, balance={await bal(U2)})")
        await drive(a, [f"cb:adm:top:{tid2}:adj"], show)   # ✏️ مبلغ آخر
        await drive(a, ["t:12.5"], show)
    else:  # قُبل المبلغ الخاطئ — نصحّح الرصيد لنكمل الاختبار
        from app.services import money as _m
        extra = await bal(U2) - D("37.50")
        if extra > 0:
            await _m.debit(U2, extra, "adjustment", note="تصحيح اختبار")
    r2 = await q1("SELECT status, amount_usd, credited_usd FROM topups WHERE id=$1", tid2) if await qv("SELECT count(*) FROM information_schema.columns WHERE table_name='topups' AND column_name='credited_usd'") else await q1("SELECT status, amount_usd FROM topups WHERE id=$1", tid2)
    print("    topup#2:", dict(r2), "balance", await bal(U2))
    expect(await bal(U2) == D("37.50"), "high", "adjust-balance", f"بعد تعديل المبلغ إلى 12.5 الرصيد = {await bal(U2)} (متوقع 37.50)")

    # شحنة 3: رفض بسبب مخصص يحوي HTML
    await drive(u, ["cb:bal:topup", "cb:bal:m:shamcash_syp", "cb:bal:amt:10"], show)
    tid3 = await qv("SELECT max(id) FROM topups WHERE user_id=$1", U2)
    await drive(u, [f"cb:bal:paid:{tid3}", "t:SC-99887766"], show)
    await drive(a, [f"cb:adm:top:{tid3}:no:custom", "t:المبلغ <ناقص> & غير مطابق"], show)
    r3 = await q1("SELECT status FROM topups WHERE id=$1", tid3)
    expect(r3["status"] == "rejected", "high", "reject", f"حالة الشحنة بعد الرفض: {r3['status']}")
    expect(await bal(U2) == D("37.50"), "critical", "reject-balance", f"الرفض غيّر الرصيد: {await bal(U2)}")
    # العميل يلغي شحنة معلّقة ثم يرسل الإثبات لها
    await drive(u, ["cb:bal:topup", "cb:bal:m:usdt_trc20", "cb:bal:amt:10"])
    tid4 = await qv("SELECT max(id) FROM topups WHERE user_id=$1", U2)
    await drive(u, [f"cb:bal:cancel:{tid4}", f"cb:bal:paid:{tid4}", "photo"], show)
    st4 = await qv("SELECT status FROM topups WHERE id=$1", tid4)
    expect(st4 in ("cancelled", "canceled", "expired"), "high", "cancelled-topup-revived", f"شحنة ملغاة عادت بعد إرسال إثبات: {st4}")
    await a.click(f"adm:top:{tid4}:ok", label="اعتماد شحنة ملغاة", expect_ok=False)
    expect(await bal(U2) == D("37.50"), "critical", "approve-cancelled", f"اعتماد شحنة ملغاة غيّر الرصيد: {await bal(U2)}")


# ─────────────────────────── S2 إعلان Meta ───────────────────────────
META_FLOW = ["cb:nav:meta", "cb:meta:pkg:trial", "cb:meta:plat:facebook", "cb:meta:goal:messages", "cb:meta:ctry:SY",
             "cb:meta:prov:damascus", "cb:meta:prov_done", "cb:meta:gender:all", "cb:meta:age:18:65", "cb:meta:aud_done",
             "t:https://facebook.com/my.shop", "t:مطعم شاورما في دمشق نريد طلبات", "cb:meta:media_done",
             "cb:meta:addon:copy:0", "t:0988123456", "cb:meta:wa_ok"]


async def meta_to_summary(u, show):
    await drive(u, ["t:/start"])
    for s in META_FLOW:
        await drive(u, [s], show)
        st = await u.state()
        # أي زر متوقع غير موجود؟ نحاول اختيار أول خيار منطقي
        if s.startswith("cb:meta:goal") and not await u.state():
            pass
    return await u.state()


async def s_meta(env, show):
    u, a = env.actor(U, "S2-user"), env.actor(A, "S2-admin")
    b0 = await bal(U)
    await meta_to_summary(u, show)
    if u.has_button("تحقق") or u.find_cb("meta:uname"):
        await drive(u, ["cb:meta:uname_skip"], show)
    cd = u.find_cb("meta:confirm")
    expect(cd, "high", "meta-no-confirm", f"لم يصل ملخص Meta لزر التأكيد. آخر نص: {clean(u.last_texts[-1] if u.last_texts else '')[:200]}")
    # نقرتان متزامنتان على «تأكيد» — هل يُخصم مرتين؟
    if cd:
        await asyncio.gather(u.click("meta:confirm", label="تأكيد (1)"), u.click("meta:confirm", label="تأكيد (2) متزامن"))
    n = await qv("SELECT count(*) FROM orders WHERE user_id=$1 AND kind='meta_campaign' AND status<>'draft'", U)
    spent = b0 - await bal(U)
    print(f"    meta orders={n} spent={spent}")
    expect(n == 1, "critical", "double-order", f"نقرتان متزامنتان على التأكيد أنشأتا {n} طلبات")
    expect(spent == D("13.00"), "critical", "double-charge", f"الخصم بعد التأكيد = {spent} (متوقع 13)")
    oid = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='meta_campaign'", U)
    # ضغطتان متتابعتان على التأكيد (بفاصل)
    await meta_to_summary(u, False)
    if u.find_cb("meta:uname"):
        await drive(u, ["cb:meta:uname_skip"])
    bx = await bal(U); nx = await qv("SELECT count(*) FROM orders WHERE user_id=$1", U)
    await u.click("meta:confirm", label="تأكيد متتابع أ"); await u.click("meta:confirm", label="تأكيد متتابع ب", expect_ok=False)
    expect(bx - await bal(U) == D("13.00"), "critical", "seq-double-charge", f"ضغطتان متتابعتان على التأكيد خصمتا {bx - await bal(U)}")
    # الأدمن يحاكي دورة نور
    for code in ("in_progress", "active", "completed"):
        await drive(a, [f"cb:adm:ord:{oid}:sim:{code}"], show)
    st = await qv("SELECT status FROM orders WHERE id=$1", oid)
    print("    meta order status after sim:", st)
    # استرداد بعد الاكتمال — هل مسموح؟ (يجب أن يُمنع أو يكون واضحاً)
    b1 = await bal(U)
    await drive(a, [f"cb:adm:ord:{oid}:refund", "t:اختبار استرداد بعد الاكتمال"], show)
    b2 = await bal(U)
    if b2 != b1:
        ISSUES.add("medium", "refund-after-complete", f"الأدمن استطاع استرداد طلب مكتمل ({st}) → الرصيد {b1}→{b2}")
    # استرداد مكرر
    await drive(a, [f"cb:adm:ord:{oid}:refund", "t:مكرر"], show)
    expect(await bal(U) == b2, "critical", "double-refund", f"استرداد مكرر: {b2}→{await bal(U)}")

    # طلب 2: رفض من نور (محاكاة) = استرداد كامل تلقائي
    await meta_to_summary(u, False)
    if u.find_cb("meta:uname"):
        await drive(u, ["cb:meta:uname_skip"])
    await drive(u, ["cb:meta:confirm"])
    oid2 = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='meta_campaign'", U)
    b3 = await bal(U)
    await drive(a, [f"cb:adm:ord:{oid2}:sim:rejected"], show)
    await drive(a, [f"cb:adm:ord:{oid2}:sim:rejected"])
    b4 = await bal(U)
    print("    reject refund:", b3, "→", b4, "status", await qv("SELECT status FROM orders WHERE id=$1", oid2))
    expect(b4 - b3 == D("13.00"), "critical", "sim-reject-refund", f"رفض نور أعاد {b4-b3} (متوقع 13 مرة واحدة)")

    # طلب 3: عميل فقير → مسودة → شحن → استئناف
    p = env.actor(U3, "S2-poor")
    from app.services import money
    before = await bal(U3)
    if before > 0:
        await money.debit(U3, before, "adjustment", note="تصفير للاختبار")
    await meta_to_summary(p, False)
    if p.find_cb("meta:uname"):
        await drive(p, ["cb:meta:uname_skip"])
    await drive(p, ["cb:meta:confirm"], show)
    dr = await q1("SELECT id,status FROM orders WHERE user_id=$1 ORDER BY id DESC LIMIT 1", U3)
    print("    poor user order:", dict(dr) if dr else None, "buttons:", [b.get("text")[:25] for b in p.last_buttons])
    await money.credit(U3, D("20"), "topup", note="شحن للاستئناف")
    await drive(p, ["cb:ord:resume"], show)
    if p.find_cb("meta:confirm"):
        await drive(p, ["cb:meta:confirm"], show)
    dr2 = await q1("SELECT id,status,price_usd FROM orders WHERE id=$1", dr["id"]) if dr else None
    print("    after resume:", dict(dr2) if dr2 else None, "balance", await bal(U3))
    expect(dr2 and dr2["status"] != "draft", "high", "resume-draft", f"استئناف المسودة لم يعمل: {dict(dr2) if dr2 else None}")


# ─────────────────────────── S3 Telegram Ads ───────────────────────────
async def s_tga(env, show):
    u, a = env.actor(U, "S3-user"), env.actor(A, "S3-admin")
    b0 = await bal(U)
    await drive(u, ["t:/start", "cb:nav:tg", "cb:tga:start", "cb:tga:budget:type"], show)
    for bad in ["9", "501", "-50", "٠"]:
        await u.text(bad)
        if await u.state() != "TgAds:budget":
            ISSUES.add("medium", "tga-budget-accepted", f"ميزانية Telegram Ads {bad!r} قُبلت")
            break
    if await u.state() == "TgAds:budget":
        await drive(u, ["t:20"], show)
    await drive(u, ["cb:tga:mode:interests", "cb:tga:int:shopping", "cb:tga:int_done"], show)
    await drive(u, ["t:" + "ع" * 161], show)   # أطول من 160
    if await u.state() != "TgAds:text":
        ISSUES.add("medium", "tga-text-160", f"نص Telegram Ads بطول 161 قُبل (الحد 160) الحالة {await u.state()}")
    else:
        await drive(u, ["t:🔥 خصم 30% على كل المنتجات <اطلب> & وفّر"], show)
    await drive(u, ["t:https://t.me/my_shop_channel"], show)
    if u.find_cb("tga:addon"):
        pass
    cd = u.find_cb("tga:confirm")
    expect(cd, "high", "tga-no-confirm", f"لم يصل ملخص TGA. آخر: {clean(u.last_texts[-1] if u.last_texts else '')[:200]}")
    await drive(u, ["cb:tga:confirm"], show)
    oid = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='tg_ads'", U)
    spent = b0 - await bal(U)
    print(f"    tga order {oid} spent {spent}")
    expect(spent == D("27.00"), "high", "tga-price", f"سعر TGA لميزانية 20 = {spent} (متوقع 27.00)")
    # الأدمن: طلب تعديل → العميل يعدّل → أُنشئ → انطلق → اكتمل بنتائج
    await drive(a, [f"cb:adm:tga:{oid}:to:revision"], show)
    if await a.state():
        await drive(a, ["t:النص يحتوي كلمة ممنوعة"], show)
    await drive(u, [f"cb:tga:revise:{oid}"], show)
    if await u.state():
        await drive(u, ["t:🔥 خصم 30% اطلب الآن"], show)
    for code in ("created", "active"):
        await drive(a, [f"cb:adm:tga:{oid}:to:{code}"], show)
    await drive(a, [f"cb:adm:tga:{oid}:to:completed"], show)
    if await a.state():
        for bad in ["abc", "-1 -1", "10"]:
            await a.text(bad)
            if not await a.state():
                ISSUES.add("medium", "tga-results-accepted", f"نتائج TGA قبلت {bad!r}")
                break
        if await a.state():
            await drive(a, ["t:12000 340"], show)
    print("    tga status:", await qv("SELECT status FROM orders WHERE id=$1", oid))
    # طلب ثانٍ ثم رفض من الأدمن = استرداد
    await drive(u, ["cb:nav:tg", "cb:tga:start", "cb:tga:budget:10", "cb:tga:mode:expert", "t:عرض خاص على الأحذية الرياضية", "t:https://t.me/shoes_shop"])
    if u.find_cb("tga:confirm"):
        await drive(u, ["cb:tga:confirm"])
    oid2 = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='tg_ads'", U)
    b1 = await bal(U)
    await drive(a, [f"cb:adm:tga:{oid2}:to:rejected"], show)
    if await a.state():
        await drive(a, ["t:مخالف للسياسة"], show)
    b2 = await bal(U)
    print("    tga reject refund", b1, "→", b2)
    expect(b2 - b1 == D("13.50"), "high", "tga-reject-refund", f"رفض TGA أعاد {b2-b1} (متوقع 13.50)")


# ─────────────────────────── S4 القنوات الشريكة ───────────────────────────
async def s_tgp(env, show):
    u, a = env.actor(U, "S4-user"), env.actor(A, "S4-admin")
    b0 = await bal(U)
    cid = await qv("SELECT min(id) FROM partner_channels")
    await drive(u, ["t:/start", "cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all", f"cb:tgp:ch:{cid}", f"cb:tgp:pick:{cid}"], show)
    await drive(u, ["cb:" + (u.find_cb("tgp:fmt:") or "tgp:fmt:none")], show)
    await drive(u, ["t:منشور <b>غير مغلق & رموز", "photo", "cb:tgp:content:next", "cb:tgp:when:asap"], show)
    await drive(u, ["cb:tgp:confirm"], show)
    oid = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='tg_post'", U)
    print(f"    tgp order {oid} spent {b0 - await bal(U)} status {await qv('SELECT status FROM orders WHERE id=$1', oid)}")
    if not oid:
        ISSUES.add("high", "tgp-order", "لم يُنشأ طلب قناة شريكة"); return
    await drive(a, [f"cb:adm:tgp:{oid}:when", "t:البارحة 10:00"], show)
    if await a.state():
        ISSUES.add("low", "tgp-past-date", "«البارحة 10:00» رُفض (جيد)" ) if False else None
        await drive(a, ["t:الآن"], show)
    await drive(a, [f"cb:adm:tgp:{oid}:url", "t:not a url"], show)
    if await a.state():
        await drive(a, ["t:https://t.me/damascus_today/555"], show)
    else:
        ISSUES.add("medium", "tgp-url-accepted", "رابط منشور «not a url» قُبل")
    await drive(a, [f"cb:adm:tgp:{oid}:views", "t:-100"], show)
    if not await a.state():
        ISSUES.add("medium", "tgp-views-negative", "مشاهدات سالبة (-100) قُبلت")
    else:
        await drive(a, ["t:1200"], show)
    await drive(a, [f"cb:adm:tgp:{oid}:finish"], show)
    print("    tgp final:", await qv("SELECT status FROM orders WHERE id=$1", oid))
    # العميل يلغي طلباً آخر ويحاول الإلغاء مرتين
    await drive(u, ["cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all", f"cb:tgp:ch:{cid}", f"cb:tgp:pick:{cid}"])
    await drive(u, ["cb:" + (u.find_cb("tgp:fmt:") or "tgp:fmt:none"), "t:منشور ثانٍ للاختبار", "cb:tgp:content:next", "cb:tgp:when:asap", "cb:tgp:confirm"])
    oid2 = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='tg_post'", U)
    b1 = await bal(U)
    await asyncio.gather(u.click(f"tgp:cancel_yes:{oid2}", label="إلغاء 1"), u.click(f"tgp:cancel_yes:{oid2}", label="إلغاء 2"))
    b2 = await bal(U)
    print("    tgp cancel refund", b1, "→", b2)
    expect(b2 - b1 in (D("13.00"), D("12.00"), D("14.00"), D("15.00")) or b2 - b1 > 0, "high", "tgp-cancel-refund", f"إلغاء القناة أعاد {b2-b1}")
    price = await qv("SELECT price_usd FROM orders WHERE id=$1", oid2)
    expect(b2 - b1 == price, "critical", "tgp-double-cancel", f"إلغاء متزامن مزدوج أعاد {b2-b1} والسعر {price}")


# ─────────────────────────── S5 التصميم ───────────────────────────
async def design_order(u, svc="copy", show=False):
    await drive(u, ["t:/start", "cb:nav:design", f"cb:add:svc:{svc}"], show)
    # نختار أول نشاط، ثم نكمل حسب ما يطلبه البوت
    for _ in range(25):
        st = await u.state()
        cds = [b.get("callback_data") or "" for b in u.last_buttons]
        if u.find_cb("ds:confirm"):
            return True
        if st == "Design:message":
            await u.text("افتتاح فرعنا الجديد في حلب مع خصم 20% لأول أسبوع")
        elif st == "Design:media":
            if u.find_cb("ds:media:none"):
                await u.click("ds:media:none")
            else:
                for _ in range(5):
                    await u.photo()
                await u.click("ds:media:done")
        elif st == "Design:brand":
            await u.click(u.find_cb("ds:brand:skip") or "ds:brand:done")
        elif st == "Design:notes":
            await u.click("ds:notes:skip")
        elif u.find_cb("ds:biz:"):
            await u.click(u.find_cb("ds:biz:"))
        elif u.find_cb("ds:lang:") and not u.find_cb("ds:lang:done"):
            await u.click(u.find_cb("ds:lang:"))
        elif u.find_cb("ds:lang:"):
            c = [x for x in cds if x.startswith("ds:lang:") and x != "ds:lang:done"]
            await u.click(c[0]) if c else None
            await u.click("ds:lang:done")
        elif u.find_cb("ds:tone:"):
            await u.click(u.find_cb("ds:tone:"))
        elif u.find_cb("ds:extras:done"):
            await u.click("ds:extras:done")
        else:
            ISSUES.add("medium", "design-stuck", f"معالج التصميم توقف: st={st} أزرار={cds[:8]} نص={clean(u.last_texts[-1] if u.last_texts else '')[:150]}")
            return False
        if show:
            print(f"      ds st={await u.state()} | {clean(u.last_texts[-1] if u.last_texts else '')[:90]}")
    return False


async def s_design(env, show):
    u, a = env.actor(U, "S5-user"), env.actor(A, "S5-admin")
    b0 = await bal(U)
    ok = await design_order(u, "copy", show)
    if not ok:
        return
    await drive(u, ["cb:ds:confirm"], show)
    oid = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind LIKE 'design%'", U) or await qv("SELECT max(id) FROM orders WHERE user_id=$1", U)
    kind, price = (await q1("SELECT kind, price_usd FROM orders WHERE id=$1", oid)).values()
    print(f"    design order {oid} kind={kind} price={price} spent={b0 - await bal(U)}")
    await drive(a, [f"cb:adm:ds:{oid}:start"], show)
    # العميل يحاول الإلغاء المجاني بعد البدء
    b1 = await bal(U)
    await drive(u, [f"cb:ds:cancel_yes:{oid}"], show)
    expect(await bal(U) == b1, "high", "cancel-after-start", f"إلغاء مجاني بعد بدء العمل أعاد مالاً {b1}→{await bal(U)}")
    # التسليم
    await drive(a, [f"cb:adm:ds:{oid}:deliver", "doc", "t:النسخة الأولى"], show)
    await drive(a, [f"cb:adm:ds:{oid}:send"], show)
    print("    after deliver:", await qv("SELECT status FROM orders WHERE id=$1", oid))
    # تعديل مجاني
    await drive(u, [f"cb:ds:revise:{oid}"], show)
    if await u.state() == "Design:revision":
        await drive(u, ["t:رجاءً غيّر اللون إلى الأزرق"], show)
    expect(await bal(U) == b1, "high", "free-revision-charged", f"التعديل الأول خُصم: {b1}→{await bal(U)}")
    await drive(a, [f"cb:adm:ds:{oid}:deliver", "doc", f"cb:adm:ds:{oid}:send"], show)
    # تعديل ثانٍ مدفوع 30%
    await drive(u, [f"cb:ds:revise:{oid}"], show)
    b2 = await bal(U)
    if await u.state() == "Design:revision":
        await drive(u, ["t:تعديل ثانٍ"], show)
    await asyncio.gather(u.click(f"ds:revise_pay:{oid}", label="دفع تعديل 1"), u.click(f"ds:revise_pay:{oid}", label="دفع تعديل 2 متزامن"))
    fee = b2 - await bal(U)
    print("    paid revision fee:", fee)
    expect(fee == (price * D("0.30")).quantize(D("0.01")), "high", "paid-revision-fee", f"رسم التعديل الثاني {fee} (متوقع 30% من {price} مرة واحدة)")
    await drive(a, [f"cb:adm:ds:{oid}:deliver", "doc", f"cb:adm:ds:{oid}:send"])
    await drive(u, [f"cb:ds:approve:{oid}"], show)
    await drive(u, [f"cb:ds:approve:{oid}"])
    print("    final:", await qv("SELECT status FROM orders WHERE id=$1", oid))
    # طلب ثانٍ: إلغاء مجاني وهو جديد + طلب ثالث: رفض الأدمن = استرداد
    await design_order(u, "design"); await drive(u, ["cb:ds:confirm"])
    o2 = await qv("SELECT max(id) FROM orders WHERE user_id=$1", U)
    b3 = await bal(U)
    await drive(u, [f"cb:ds:cancel_order:{o2}", f"cb:ds:cancel_yes:{o2}"], show)
    print("    free cancel refund:", await bal(U) - b3)
    await design_order(u, "reel"); await drive(u, ["cb:ds:confirm"])
    o3 = await qv("SELECT max(id) FROM orders WHERE user_id=$1", U)
    b4 = await bal(U)
    await drive(a, [f"cb:adm:ds:{o3}:reject", "t:المواد غير كافية"], show)
    print("    reject refund:", await bal(U) - b4, "status", await qv("SELECT status FROM orders WHERE id=$1", o3))
    # الأدمن يسلّم طلباً مرفوضاً؟
    await drive(a, [f"cb:adm:ds:{o3}:deliver", "doc", f"cb:adm:ds:{o3}:send"], show)
    st = await qv("SELECT status FROM orders WHERE id=$1", o3)
    expect(st in ("rejected", "refunded", "failed", "cancelled"), "high", "deliver-after-reject", f"تسليم طلب مرفوض غيّر حالته إلى {st}")


# ─────────────────────────── S6 الاشتراك المجدول ───────────────────────────
async def s_sched(env, show):
    u, a = env.actor(U, "S6-user"), env.actor(A, "S6-admin")
    b0 = await bal(U)
    await drive(u, ["t:/start", "cb:sub:list", "cb:sub:pkg:daily7", "cb:sub:buy:daily7"], show)
    scd = u.find_cb("sub:confirm:")
    await asyncio.gather(u.click(scd, label="شراء 1"), u.click(scd, label="شراء 2 متزامن"))
    bq = await bal(U)
    await u.click(scd, label="شراء 3 متتابع (زر قديم)", expect_ok=False)
    # زر من الإصدار القديم بلا رمز: يجب أن يعيد المراجعة لا أن يشتري
    await u.click("sub:confirm:daily7", label="زر قديم بلا رمز", expect_ok=False)
    expect(await bal(U) == bq, "high", "seq-double-subscription", f"الضغط مجدداً على زر تأكيد الشراء القديم خصم {bq - await bal(U)} واشترى باقة أخرى بلا تأكيد جديد")
    n = await qv("SELECT count(*) FROM scheduled_subscriptions WHERE user_id=$1", U)
    print(f"    subs={n} spent={b0 - await bal(U)}")
    expect(n == 1, "high", "double-subscription", f"نقرتان متزامنتان أنشأتا {n} اشتراك/ات وخصم {b0 - await bal(U)}")
    sid = await qv("SELECT max(id) FROM scheduled_subscriptions WHERE user_id=$1", U)
    # الأدمن يضيف محتوى عبر البوت
    await drive(a, [f"cb:adm:sub:{sid}:add"], show)
    for inp in ["0", "8", "1"]:
        if await a.state() == "ScheduledAdmin:seq":
            await a.text(inp)
            if inp in ("0", "8") and await a.state() != "ScheduledAdmin:seq":
                ISSUES.add("medium", "sched-seq-accepted", f"رقم التسليم {inp!r} قُبل لباقة من 7")
    if await a.state() == "ScheduledAdmin:file":
        await drive(a, ["t:ليس ملفاً", "photo"], show)
    if await a.state() == "ScheduledAdmin:copy":
        await drive(a, ["t:نص اليوم الأول <b>مهم</b>"], show)
    items = await qv("SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=$1", sid)
    print("    items:", items)
    # تفعيل بتاريخ اليوم وتشغيل المجدول
    from app.services import scheduled as S
    from datetime import date, timedelta
    # التفعيل قبل اكتمال التصاميم يجب أن يُرفض (مقصود) — المشكلة فقط لو قُبل
    try:
        await S.activate(sid, (date.today() - timedelta(days=1)).isoformat(), "00:01")
        if items < 7:
            ISSUES.add("high", "sched-activate-incomplete", f"تفعيل اشتراك ينقصه {7 - items} تصاميم قُبل")
    except ValueError as e:
        print("    activate refused as expected:", e)
    except Exception as e:  # noqa: BLE001
        ISSUES.add("medium", "sched-activate", f"تفعيل الاشتراك: {type(e).__name__}: {e}")
    from app.services import scheduler
    fn = [n for n in dir(scheduler) if "sched" in n.lower() or "due" in n.lower() or "tick" in n.lower()]
    print("    scheduler functions:", fn)
    # إلغاء مع استرداد جزئي
    b1 = await bal(U)
    r = await S.cancel(sid, A)
    r2 = await S.cancel(sid, A)
    print("    cancel refund:", await bal(U) - b1, "second cancel:", bool(r2))
    expect((await bal(U) - b1) <= D("30"), "critical", "sched-over-refund", f"استرداد الإلغاء {await bal(U) - b1} أكبر من السعر")


# ─────────────────────────── S7 التذاكر ───────────────────────────
async def s_tickets(env, show):
    u, a = env.actor(U3, "S7-user"), env.actor(A, "S7-admin")
    await drive(u, ["t:/start", "cb:sup:menu", "cb:sup:new", "cb:tck:open:general", "t:<i>مشكلة</i> في & الدفع"], show)
    tid = await qv("SELECT max(id) FROM tickets WHERE user_id=$1", U3)
    expect(tid, "high", "ticket-create", "لم تُنشأ تذكرة")
    if not tid:
        return
    await drive(a, ["cb:adm:tickets", f"cb:adm:tck:{tid}:view", f"cb:adm:tck:{tid}:reply", "t:أهلاً <b>سام</b>، نتابع"], show)
    await drive(u, [f"cb:tck:reply:{tid}", "photo"], show)
    await drive(u, ["cb:sup:mine", f"cb:tck:view:{tid}"], show)
    await drive(a, [f"cb:adm:tck:{tid}:close"], show)
    await drive(u, [f"cb:tck:reply:{tid}", "t:رد بعد الإغلاق"], show)
    print("    ticket:", await qv("SELECT status FROM tickets WHERE id=$1", tid))
    # تذكرة لطلب ليس ملكه
    other = await qv("SELECT max(id) FROM orders WHERE user_id=$1", U)
    if other:
        await drive(u, [f"cb:tck:open:order:{other}", "t:طلب شخص آخر"], show)
        t2 = await q1("SELECT id, order_id FROM tickets WHERE user_id=$1 ORDER BY id DESC LIMIT 1", U3)
        expect(not t2 or t2["order_id"] != other, "high", "idor-ticket", f"العميل فتح تذكرة على طلب مستخدم آخر #{other}")
    # عرض طلب مستخدم آخر (IDOR)
    if other:
        start = len(sent)
        await u.click(f"ord:view:{other}", expect_ok=False)
        leak = [d for n, d in sent[start:] if str(d.get("chat_id")) == str(U3) and f"#ORD-{other}" in (d.get("text") or "")]
        expect(not leak, "critical", "idor-order-view", f"العميل رأى تفاصيل طلب مستخدم آخر #ORD-{other}")
        await u.click(f"ord:media:{other}", expect_ok=False)
        leak2 = [n for n, d in sent[start:] if n in ("SendPhoto", "CopyMessage", "SendMediaGroup", "SendDocument") and str(d.get("chat_id")) == str(U3)]
        expect(not leak2, "critical", "idor-order-media", f"العميل استلم ملفات طلب مستخدم آخر #{other}")
        await u.click(f"ds:files:{other}", expect_ok=False)
        leak3 = [n for n, d in sent[start:] if n in ("SendPhoto", "CopyMessage", "SendMediaGroup", "SendDocument") and str(d.get("chat_id")) == str(U3)]
        expect(not leak3, "critical", "idor-design-files", f"العميل استلم ملفات تسليم تصميم لمستخدم آخر #{other}")
        b = await bal(U)
        await u.click(f"ds:cancel_yes:{other}", expect_ok=False); await u.click(f"tgp:cancel_yes:{other}", expect_ok=False)
        await u.click(f"ord:draft_cancel:{other}", expect_ok=False); await u.click(f"ds:approve:{other}", expect_ok=False)
        expect(await bal(U) == b, "critical", "idor-cancel", "عميل ألغى/اعتمد طلب مستخدم آخر")
    tk_other = await qv("SELECT max(id) FROM tickets WHERE user_id<>$1", U3)
    if tk_other:
        start = len(sent)
        await u.click(f"tck:view:{tk_other}", expect_ok=False)
        leak = [d for n, d in sent[start:] if str(d.get("chat_id")) == str(U3) and f"TCK-{tk_other}" in (d.get("text") or "")]
        expect(not leak, "critical", "idor-ticket-view", "العميل رأى تذكرة مستخدم آخر")


# ─────────────────────────── S8 أدوات الأدمن ───────────────────────────
async def s_admin_tools(env, show):
    a = env.actor(A, "S8-admin")
    await drive(a, ["t:/admin", "cb:adm:find", "t:@samer"], show)
    await drive(a, ["cb:adm:find", f"t:{U}"], show)
    oid = await qv("SELECT max(id) FROM orders")
    await drive(a, ["cb:adm:find", f"t:#ORD-{oid}"], show)
    await drive(a, ["cb:adm:find", "t:@nobody_here"], show)
    await drive(a, ["cb:adm:find", "t:'; DROP TABLE users; --"], show)
    expect(await qv("SELECT count(*) FROM users") > 0, "critical", "sqli", "جدول المستخدمين اختفى!")
    b0 = await bal(U2)
    # خصم أكبر من الرصيد
    await drive(a, [f"cb:adm:user:{U2}:sub", f"t:{b0 + 100} خصم كبير"], show)
    if a.find_cb(f"adm:bal:confirm:{U2}"):
        await drive(a, ["cb:" + a.find_cb(f"adm:bal:confirm:{U2}")], show)
    expect(await bal(U2) >= 0, "critical", "negative-by-admin", f"خصم الأدمن جعل الرصيد سالباً: {await bal(U2)}")
    for bad in ["-5 سالب", "abc", "5", "0 صفر", "1e6 كبير"]:
        await drive(a, [f"cb:adm:user:{U2}:add", f"t:{bad}"])
        if a.find_cb(f"adm:bal:confirm:{U2}"):
            ISSUES.add("medium" if bad != "5" else "low", "balance-input-accepted", f"تعديل الرصيد قبل {bad!r} ووصل لشاشة التأكيد")
        await a.text("/cancel")
    # تأكيد مزدوج متزامن للإضافة
    await drive(a, [f"cb:adm:user:{U2}:add", "t:7 تعويض"])
    b1 = await bal(U2)
    bcd = a.find_cb(f"adm:bal:confirm:{U2}")
    await asyncio.gather(a.click(bcd, label="تأكيد 1"), a.click(bcd, label="تأكيد 2"))
    await a.click(bcd, label="تأكيد 3 (قديم)", expect_ok=False)
    await a.click(f"adm:bal:confirm:{U2}", label="تأكيد بصيغة قديمة بلا رمز", expect_ok=False)
    expect(await bal(U2) - b1 == D("7"), "critical", "double-balance-confirm", f"تأكيد مكرر للإضافة أضاف {await bal(U2) - b1}")
    # تأكيد متتابع (غير متزامن) لنفس الشاشة
    await drive(a, [f"cb:adm:user:{U2}:add", "t:3 تعويض ثانٍ"])
    b1 = await bal(U2)
    bcd = a.find_cb(f"adm:bal:confirm:{U2}")
    await a.click(bcd, label="تأكيد أ"); await a.click(bcd, label="تأكيد ب (متتابع)", expect_ok=False)
    expect(await bal(U2) - b1 == D("3"), "critical", "seq-double-balance-confirm", f"ضغطتان متتابعتان على تأكيد الإضافة أضافتا {await bal(U2) - b1}")
    # الحظر ثم محاولة الاستخدام
    await drive(a, [f"cb:adm:user:{U2}:toggle"], show)
    blocked = await qv("SELECT is_blocked FROM users WHERE tg_id=$1", U2)
    ub = env.actor(U2, "S8-blocked")
    await drive(ub, ["t:/start", "cb:bal:topup"], show)
    print("    blocked:", blocked, "last:", clean(ub.last_texts[-1] if ub.last_texts else "")[:80])
    await drive(a, [f"cb:adm:user:{U2}:toggle"])
    # بث مع مستخدم حظر البوت
    BLOCKED_CHATS.add(U3)
    await drive(a, ["cb:adm:bc", "t:🎉 {name} عرض <b>خاص</b>", "photo"], show)
    await drive(a, [a.find_cb("adm:bc:aud:all") and "cb:adm:bc:aud:all" or "cb:adm:bc:aud:new"], show)
    await drive(a, ["cb:adm:bc:confirm"], show)
    if a.find_cb("adm:bc:confirm2"):
        await drive(a, ["cb:adm:bc:confirm2"], show)
    await asyncio.sleep(0.5)
    print("    U3 marked blocked_bot:", await qv("SELECT is_blocked_bot FROM users WHERE tg_id=$1", U3))
    bc = [d for n, d in sent if n in ("SendPhoto", "SendMessage") and "عرض" in (d.get("caption") or d.get("text") or "") and "{name}" in (d.get("caption") or d.get("text") or "")]
    expect(not bc, "medium", "broadcast-placeholder", "العنصر {name} لم يُستبدل في البث")
    BLOCKED_CHATS.discard(U3)
    # المحافظ: تعديل عنوان غير صالح
    await drive(a, ["cb:adm:wallets", "cb:adm:wal:usdt_trc20", "cb:adm:wal:usdt_trc20:edit", "t:hello world this is not an address"], show)
    await drive(a, ["t:0x1234567890123456789012345"], show)  # عنوان BEP20 وُضع في TRC20
    if not await a.state():
        ISSUES.add("medium", "wallet-format", "عنوان TRC20 يقبل أي نص طوله 20+ بلا مسافات (مثل عنوان 0x… من شبكة أخرى) — خطر ضياع أموال العملاء")
    else:
        await a.text("/cancel")
    # عنوان TRC20 صحيح ← شاشة مراجعة أولاً، ولا يُحفظ قبل «✅ العنوان صحيح»
    from app.services import payments as _PM
    good = "TXYZabcdefghijkmnopqrstuvwxyz1234"   # 34 حرفاً Base58 يبدأ بـ T
    good = "T" + "R7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6"[:33].ljust(33, "a")
    await drive(a, ["cb:adm:wal:usdt_trc20", "cb:adm:wal:usdt_trc20:edit", f"t:{good}"], show)
    saved_early = (await _PM.get_methods())["usdt_trc20"].get("address") == good
    expect(await a.state() == "AdminTopup:wallet_confirm" and not saved_early, "high", "wallet-no-confirm",
           f"عنوان المحفظة حُفظ بلا مراجعة (state={await a.state()})")
    await drive(a, ["cb:adm:wal:usdt_trc20:save"], show)
    expect((await _PM.get_methods())["usdt_trc20"].get("address") == good, "high", "wallet-save",
           "العنوان لم يُحفظ بعد التأكيد")
    # سعر الصرف: تغيّر كبير يحتاج تأكيداً
    await drive(a, ["cb:adm:rate", "t:13000"], show)
    if await a.state() == "AdminTopup:rate_confirm":
        await drive(a, ["cb:adm:rate:save"], show)
    await drive(a, ["cb:adm:rate", "t:130"], show)   # صفران ناقصان
    expect(await a.state() == "AdminTopup:rate_confirm" and await _PM.syp_rate() == D("13000"), "high", "rate-no-confirm",
           f"سعر 130 بدل 13000 حُفظ بلا تأكيد (rate={await _PM.syp_rate()})")
    await a.text("/cancel")
    await drive(a, ["cb:adm:rate", "t:0"], show)
    if not await a.state():
        ISSUES.add("medium", "syp-rate-zero", "سعر الصرف 0 قُبل")
    await a.text("/cancel")
    await drive(a, ["cb:adm:stats", "cb:adm:tasks", "cb:adm:orders", "cb:adm:topups", "cb:adm:settings", "cb:adm:svcs", "cb:adm:ch:menu"], show)
    # الأدمن الثاني يرى البطاقات؟
    a2 = env.actor(ADMIN2, "S8-admin2")
    await drive(a2, ["t:/admin", "cb:adm:stats"], show)


# ─────────────────────────── S9 سباقات الأدمن ───────────────────────────
async def s_races(env, show):
    u, a, a2 = env.actor(U2, "S9-user"), env.actor(A, "S9-admin1"), env.actor(ADMIN2, "S9-admin2")
    await drive(u, ["t:/start", "cb:bal:topup", "cb:bal:m:usdt_trc20", "cb:bal:amt:10"])
    tid = await qv("SELECT max(id) FROM topups WHERE user_id=$1", U2)
    await drive(u, [f"cb:bal:paid:{tid}", "photo"])
    b0 = await bal(U2)
    await asyncio.gather(a.click(f"adm:top:{tid}:ok", label="أدمن1 اعتماد"), a2.click(f"adm:top:{tid}:ok", label="أدمن2 اعتماد"),
                         a.click(f"adm:top:{tid}:no:unclear", label="أدمن1 رفض متزامن"))
    diff = await bal(U2) - b0
    st = await qv("SELECT status FROM topups WHERE id=$1", tid)
    print(f"    race topup: +{diff} status={st}")
    expect(diff in (D("0"), D("10")), "critical", "race-topup", f"اعتماد متزامن من أدمنين أضاف {diff}")
    expect((st == "approved") == (diff == D("10")), "critical", "race-topup-state", f"الحالة {st} لا تطابق الرصيد (+{diff})")
    # استرداد متزامن لطلب
    from app.services import money
    await money.credit(U2, D("50"), "topup", note="race")
    await meta_to_summary(u, False)
    if u.find_cb("meta:uname"):
        await drive(u, ["cb:meta:uname_skip"])
    await drive(u, ["cb:meta:confirm"])
    oid = await qv("SELECT max(id) FROM orders WHERE user_id=$1 AND kind='meta_campaign'", U2)
    b1 = await bal(U2)
    await asyncio.gather(a.click(f"adm:ord:{oid}:sim:rejected", label="رفض1"), a2.click(f"adm:ord:{oid}:sim:rejected", label="رفض2"))
    print("    race refund:", await bal(U2) - b1)
    expect(await bal(U2) - b1 == D("13.00"), "critical", "race-refund", f"رفض متزامن أعاد {await bal(U2) - b1}")


# ─────────────────────────── S10 التنقل والمقاطعة ───────────────────────────
async def s_nav(env, show):
    u = env.actor(U, "S10-user")
    await drive(u, ["t:/start", "cb:nav:meta", "cb:meta:pkg:custom", "t:/start"], show)
    expect(not await u.state(), "medium", "start-keeps-state", f"/start لم يُنهِ المعالج: {await u.state()}")
    await drive(u, ["cb:nav:meta", "cb:meta:pkg:custom", "t:/balance"], show)
    await drive(u, ["t:/orders", "t:/help", "t:/id", "t:/cancel", "t:/cancel", "t:/admin", "t:/cpanel"], show)
    await drive(u, ["t:🏠 القائمة", "t:مرحبا", "photo"], show)
    await u.sticker()
    await drive(u, ["cb:ord:list:999", "cb:bal:hist:999", "cb:bal:hist:0", "cb:bal:hist:-1", "cb:ord:list:0", "cb:ord:view:999999",
                    "cb:tgp:ch:999999", "cb:tgp:pick:999999", "cb:sub:pkg:nope", "cb:sub:confirm:nope", "cb:meta:pkg:nope",
                    "cb:meta:ctry:XX", "cb:tga:budget:-5", "cb:tga:budget:abc", "cb:bal:amt:-5", "cb:bal:amt:0", "cb:bal:amt:abc",
                    "cb:bal:m:nope", "cb:ds:bundle:99", "cb:ds:biz:nope", "cb:meta:daily:-1", "cb:meta:days:0", "cb:meta:age:65:18",
                    "cb:bal:amt:1e9", "cb:bal:amt:1000000"], show)
    tid = await qv("SELECT max(id) FROM topups WHERE user_id=$1 AND amount_usd>=1000000", U)
    expect(not tid, "high", "forged-amount", "زر مزوّر bal:amt:1000000 أنشأ طلب شحن بمبلغ مليون")


SCEN = {"topup": s_topup, "meta": s_meta, "tga": s_tga, "tgp": s_tgp, "design": s_design, "sched": s_sched,
        "tickets": s_tickets, "admin": s_admin_tools, "races": s_races, "nav": s_nav}


async def main():
    names = [x for x in sys.argv[1:] if not x.startswith("-")] or list(SCEN)
    show = "-v" in sys.argv
    env = await Env(verbose=True, logfile=f"{OUT}/scenarios_log.txt").boot()
    await seed(env)
    for n in names:
        print(f"\n══ سيناريو {n}")
        try:
            await SCEN[n](env, show)
        except Exception as e:  # noqa: BLE001
            import traceback
            ISSUES.add("medium", "scenario-aborted", f"{n}: {type(e).__name__}: {e} | {traceback.format_exc()[-500:]}", where=f"scenario {n}")
    from app.db import pool as db
    rows = await db.fetch("SELECT u.tg_id, u.balance_usd, COALESCE(SUM(l.amount_usd),0) AS s FROM users u LEFT JOIN ledger l ON l.user_id=u.tg_id GROUP BY 1,2")
    for r in rows:
        if D(r["balance_usd"]) != D(r["s"]):
            ISSUES.add("critical", "ledger-mismatch", f"{r['tg_id']}: رصيد {r['balance_usd']} ≠ دفتر {r['s']}", where="integrity")
        if D(r["balance_usd"]) < 0:
            ISSUES.add("critical", "negative-balance", f"{r['tg_id']}: {r['balance_usd']}", where="integrity")
    report(f"{OUT}/scenario_issues.json", "نتيجة السيناريوهات")
    await env.close()



async def s_races2(env, show):
    """نقرتان متزامنتان على «تأكيد» في كل معالج دفع."""
    u = env.actor(U, "S11-user")
    await drive(u, ["t:/start"])
    async def dbl(label, cd):
        b = await bal(U); n = await qv("SELECT count(*) FROM orders WHERE user_id=$1 AND status NOT IN ('draft','awaiting_payment')", U)
        await asyncio.gather(u.click(cd, label=f"{label} 1"), u.click(cd, label=f"{label} 2"))
        spent = b - await bal(U); n2 = await qv("SELECT count(*) FROM orders WHERE user_id=$1 AND status NOT IN ('draft','awaiting_payment')", U)
        print(f"    {label}: orders +{n2-n} spent {spent}")
        expect(n2 - n == 1, "critical", "double-order", f"{label}: نقرتان متزامنتان أنشأتا {n2-n} طلبات وخصمتا {spent}")
    await drive(u, ["cb:nav:tg", "cb:tga:start", "cb:tga:budget:10", "cb:tga:mode:expert", "t:عرض خاص على الأحذية الرياضية", "t:https://t.me/shoes_shop"])
    await dbl("Telegram Ads", "tga:confirm")
    cid = await qv("SELECT min(id) FROM partner_channels")
    await drive(u, ["cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all", f"cb:tgp:ch:{cid}", f"cb:tgp:pick:{cid}"])
    await drive(u, ["cb:" + (u.find_cb("tgp:fmt:") or "x"), "t:منشور تجريبي", "cb:tgp:content:next", "cb:tgp:when:asap"])
    await dbl("قناة شريكة", "tgp:confirm")
    await design_order(u, "copy")
    await dbl("تصميم", "ds:confirm")

SCEN["races2"] = s_races2

if __name__ == "__main__":
    asyncio.run(main())
