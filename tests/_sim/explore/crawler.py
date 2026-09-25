"""مستكشف آلي: يضغط كل زر يظهر، ويجرّب في كل خطوة إدخال مدخلات سيئة ثم صالحة.

تشغيل:  /tmp/freshdb.sh && python crawler.py
"""
from __future__ import annotations

import asyncio, os, random, re, sys
from decimal import Decimal

from harness import *  # noqa
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT, exist_ok=True)

random.seed(7)

# مدخلات صالحة لكل حالة (تُجرَّب بالترتيب حتى تتغير الحالة)
VALID: dict[str, list] = {
    "Topup:amount": ["25"],
    "Topup:proof": ["photo", "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"],
    "Meta:daily": ["3"], "Meta:days": ["5"],
    "Meta:link": ["https://facebook.com/my.shop.page"],
    "Meta:desc": ["مطعم شاورما في دمشق، نريد طلبات توصيل للمنازل"],
    "Meta:media": ["photo", "btn:meta:media_done"],
    "Meta:whatsapp": ["0988123456", "btn:meta:wa_ok"],
    "TgAds:budget": ["25"], "TgAds:channels": ["@durov @telegram"],
    "TgAds:text": ["🔥 خصم 30% على كل المنتجات — اطلب الآن"], "TgAds:link": ["https://t.me/my_shop_channel"],
    "TgRevise:text": ["نص معدّل: خصم 40% لفترة محدودة"],
    "TgPost:content": ["منشور تجريبي عن متجرنا 🌟 https://t.me/my_shop", "btn:tgp:content:next"],
    "TgPost:when": ["غداً 20:00"],
    "Design:message": ["افتتاح فرعنا الجديد في حلب مع خصم 20% لأول أسبوع"],
    "Design:media": ["photo", "photo", "photo", "photo", "photo", "btn:ds:media:done", "btn:ds:media:none"],
    "Design:brand": ["photo", "أزرق وذهبي #1E40AF", "btn:ds:brand:done", "btn:ds:brand:skip"],
    "Design:notes": ["بدون ملاحظات إضافية", "btn:ds:notes:skip"],
    "Design:revision": ["رجاءً غيّر لون الخلفية إلى أبيض"],
    "ClientTicket:message": ["عندي مشكلة بطلبي، لم يبدأ بعد"], "ClientTicket:reply": ["شكراً، بانتظاركم"],
    # الأدمن
    "AdminOrder:refund_reason": ["الصفحة مخالفة لسياسات ميتا"], "AdminOrder:message_user": ["مرحباً، نحتاج رابطاً آخر"],
    "AdminOrder:fallback_username": ["@support_demo"], "AdminOrder:tga_revision": ["النص طويل، اختصره رجاءً"],
    "AdminOrder:tga_reject": ["مخالف لسياسة تيليغرام"], "AdminOrder:tga_results": ["12000 340"],
    "AdminOrder:tga_text": ["نص معدّل من الأدمن"], "AdminOrder:tgp_when": ["الآن"],
    "AdminOrder:tgp_url": ["https://t.me/partner_ch/123"], "AdminOrder:tgp_views": ["850"],
    "AdminOrder:tgp_reject": ["القناة رفضت المحتوى"], "AdminOrder:tgp_text": ["نص معدّل"],
    "AdminDesign:deliver": ["document", "btn:send", "btn:أرسل"], "AdminDesign:reject": ["لا نستطيع تنفيذ هذا الطلب"],
    "ScheduledAdmin:seq": ["1"], "ScheduledAdmin:file": ["photo"], "ScheduledAdmin:copy": ["نص اليوم الأول"],
    "AdminTools:ticket_reply": ["أهلاً، نعمل على طلبك"], "AdminTools:search": ["@samer"],
    "AdminTools:balance_input": ["5 تعويض اختبار"], "AdminTools:broadcast_text": ["🎉 {name} عرض خاص"],
    "AdminTools:broadcast_photo": ["photo"], "AdminTopup:reject_reason": ["الإثبات غير واضح"],
    "AdminTopup:adjust_amount": ["20"], "AdminTopup:message_user": ["أرسل صورة أوضح"],
    "AdminTopup:wallet_address": ["TQ7mYkWn2cX8yR5vB3nH6jL1pD4sF9gA0e", "0x52908400098527886E0F7030069857D2E4169EE7", "123456789"],
    "AdminTopup:wallet_holder": ["رأفت أحمد"], "AdminTopup:syp_rate": ["13000"],
    # 💼 سوق القنوات
    "AdminMp:reason": ["سبب واضح من الأدمن"], "MpReg:ref": ["@damascus_deals"], "MpReg:p24": ["10"], "MpReg:p48": ["15"],
    "MpReg:ppin": ["14"], "MpReg:blurb": ["عروض يومية"], "MpOrder:when": ["بعد 2 ساعة"], "MpPay:address": ["0933123456"],
    "MpPay:amount": ["10"], "MpPay:cv_amount": ["5"],
}

FUZZ_TEXT = ["0", "-5", "abc", "٥٠", "1e309", "99999999999999999999", "5,5", "NaN", "inf",
             "<b>x</b> & <script>", "😀" * 30, "x" * 4096, "http://", "' OR 1=1 --", "\u202e‮عكسي"]
FUZZ_MEDIA = ["photo", "document", "video", "sticker"]

SKIP_PREFIX = ()  # لا شيء — بيئة معزولة، نجرّب كل شيء
PER_PREFIX_CAP = 6
# حقول نص حر: قبول «0» أو «abc» فيها طبيعي
# «٥٠» = 50 بأرقام عربية: قبوله صحيح (تطبيع مقصود) — لا يُحسب قبولاً خاطئاً
VALID_EQUIV = {"٥٠"}
FREE_TEXT = {"TgPost:when", "MpReg:blurb", "MpPay:address", "AdminMp:reason", "AdminOrder:refund_reason", "AdminOrder:message_user", "AdminOrder:tga_revision", "AdminOrder:tga_reject",
             "AdminOrder:tga_text", "AdminOrder:tgp_reject", "AdminOrder:tgp_text", "AdminDesign:reject",
             "AdminTools:ticket_reply", "AdminTools:broadcast_text", "AdminTopup:reject_reason", "AdminTopup:message_user",
             "AdminTopup:wallet_holder", "ClientTicket:message", "ClientTicket:reply", "Design:notes", "Design:revision",
             "TgRevise:text", "TgAds:text", "ScheduledAdmin:copy", "AdminTools:search", "Design:brand",
             "AdminTools:broadcast_photo", "TgPost:content", "Design:message", "Meta:desc", "Meta:media", "Design:media"}


def norm(cd: str) -> str:
    return re.sub(r"\d+", "#", cd)


def prefix(cd: str) -> str:
    return ":".join(norm(cd).split(":")[:2])


class Crawler:
    def __init__(self, actor: Actor, max_steps: int, fuzz=True):
        self.a, self.max_steps, self.fuzz = actor, max_steps, fuzz
        self.visited: set[str] = set()
        self.prefix_count: dict[str, int] = {}
        self.fuzzed_states: set[str] = set()
        self.steps = 0
        self.states_seen: set[str] = set()

    async def feed(self, item):
        a = self.a
        if item == "photo":
            return await a.photo()
        if item == "document":
            return await a.document()
        if item == "video":
            return await a.video()
        if item == "sticker":
            return await a.sticker()
        if item.startswith("btn:"):
            key = item[4:]
            cd = a.find_cb(key)
            if cd:
                return await a.click(cd)
            for b in a.last_buttons:
                if key in b.get("text", "") and b.get("callback_data"):
                    return await a.click(b["callback_data"])
            return None
        return await a.text(item)

    async def handle_state(self):
        st = await self.a.state()
        guard = 0
        while st and guard < 8:
            guard += 1
            self.states_seen.add(st)
            if self.fuzz and st not in self.fuzzed_states:
                self.fuzzed_states.add(st)
                accepted = None
                for f in FUZZ_TEXT + FUZZ_MEDIA:
                    await self.feed(f)
                    if await self.a.state() != st:
                        accepted = f
                        break   # مدخل «سيئ» قُبل! نسجّله إن كان رقماً سالباً/صفراً في حقل مبلغ
                now = await self.a.state()
                if now != st and st not in FREE_TEXT and accepted not in VALID_EQUIV and not str(now).endswith("_confirm"):
                    ISSUES.add("medium", "fuzz-accepted", f"الحالة {st} قبلت المدخل {str(accepted)[:30]!r} وانتقلت إلى {now}")
                    st = now
                    continue
            vals = VALID.get(st)
            if st.endswith((":choosing", ":broadcast_audience", ":broadcast_confirm", "_confirm")):
                return  # خطوات بالأزرار فقط
            if not vals:
                ISSUES.add("low", "crawler-no-input", f"لا يوجد مدخل صالح معروف للحالة {st}")
                return
            moved = False
            for v in vals:
                await self.feed(v)
                if await self.a.state() != st:
                    moved = True
                    break
            if not moved:
                # كثير من الحالات تبقى بانتظار زر (مثل تأكيد) — نخرج ونترك الأزرار للمستكشف
                return
            st = await self.a.state()

    async def run(self):
        a = self.a
        await a.text("/start"); await a.click("nav:accept")
        while self.steps < self.max_steps:
            cand = [b["callback_data"] for b in reversed(a.all_buttons) if b.get("callback_data")]
            nxt = None
            for cd in cand:
                if cd in self.visited:
                    continue
                if self.prefix_count.get(prefix(cd), 0) >= PER_PREFIX_CAP and norm(cd) in {norm(v) for v in self.visited}:
                    self.visited.add(cd); continue
                nxt = cd; break
            if not nxt:
                break
            self.visited.add(nxt)
            self.prefix_count[prefix(nxt)] = self.prefix_count.get(prefix(nxt), 0) + 1
            self.steps += 1
            await a.click(nxt)
            await self.handle_state()
        return self


async def seed(env):
    """تحضير: محافظ، سعر صرف، قناة شريكة، باقة مجدولة، أرصدة."""
    from app.db.repo import users, partner_channels as PC
    from app.services import money, payments, partner_posts, scheduled as sched_svc, pricing, cpanel
    for uid in (A, ADMIN2, U, U2, U3):
        await users.upsert_user(uid, tg_user(uid).full_name, tg_user(uid).username)
    m = await payments.get_methods()
    m["usdt_trc20"]["address"] = "TQ7mYkWn2cX8yR5vB3nH6jL1pD4sF9gA0e"
    m["usdt_bep20"]["address"] = "0x52908400098527886E0F7030069857D2E4169EE7"
    m["shamcash_usd"].update(address="123456789", holder="رأفت")
    m["shamcash_syp"].update(address="987654321", holder="رأفت")
    await payments.save_methods(m); await payments.set_syp_rate(Decimal("13000"))
    await PC.create(partner_posts.validate_channel({
        "title": "قناة دمشق اليوم", "url": "@damascus_today", "category": "news", "subscribers": 45000,
        "avg_views": 9000, "blurb": "أخبار دمشق", "price_24h": "10", "price_48h": "15", "price_pin": "25"}))
    await sched_svc.save_package({"code": "daily7", "title": "أسبوع تصاميم", "description": "7 تصاميم", "price_usd": "30",
                                  "total_items": 7, "duration_days": 7, "send_time": "20:00"})
    await money.credit(U, Decimal("1000"), "topup", note="seed")
    await money.credit(U3, Decimal("200"), "topup", note="seed")
    # نور يشترط telegram_username: التشغيل الحقيقي يضبط معرّفاً احتياطياً للعملاء بلا معرّف (U2)
    # (حالة «بلا معرّف احتياطي» مغطاة في tests/_sim/review3/sim_no_username.py)
    from app.db.repo import settings as settings_repo
    await settings_repo.set_("admin_fallback_username", "tarweej_admin")
    await pricing.refresh(); await cpanel.refresh_runtime()


async def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    env = await Env(verbose=True, logfile=os.path.join(OUT, "crawl_log.txt")).boot()
    await seed(env)
    print("══ 1) العميل الغني (U) يستكشف كل شيء")
    u = await Crawler(env.actor(U, "user-rich"), steps).run()
    print(f"   خطوات={u.steps} حالات={len(u.states_seen)}")
    print("══ 2) العميل الفقير (U2) — رصيد 0")
    u2 = await Crawler(env.actor(U2, "user-poor"), steps // 2).run()
    print("══ 3) عميل باسم يحوي HTML (U3)")
    u3 = await Crawler(env.actor(U3, "user-evil-name"), steps // 2, fuzz=False).run()
    print("══ 4) الأدمن يستكشف ويعالج كل ما أنشأه العملاء")
    adm = await Crawler(env.actor(A, "admin"), steps).run()
    print(f"   خطوات={adm.steps} حالات={len(adm.states_seen)}")
    print("══ 5) العميل يعود بعد تصرفات الأدمن")
    u.max_steps += steps // 2
    await u.run()
    print("══ 6) أمان: عميل عادي يضغط أزرار الأدمن")
    admin_cds = sorted({b["callback_data"] for b in adm.a.all_buttons if (b.get("callback_data") or "").startswith("adm:")})
    from app.db.repo import users
    bal_before = await users.get_balance(U2)
    evil = env.actor(U2, "security")
    await evil.text("/start")
    leaked = []
    for cd in admin_cds:
        start = len(sent)
        await evil.click(cd, expect_ok=False)
        out = sent[start:]
        if any(n in ("SendMessage", "EditMessageText") and str(d.get("chat_id")) == str(U2) for n, d in out if FALLBACK_BTN not in (d.get("text") or "")):
            leaked.append(cd)
        if await evil.state():
            st = await evil.state()
            ISSUES.add("critical", "admin-state-leak", f"عميل عادي دخل حالة أدمن {st} عبر {cd}")
            await evil.text("99999 اختراق")
            await evil.text("/cancel")
    bal_after = await users.get_balance(U2)
    if bal_after != bal_before:
        ISSUES.add("critical", "privilege-escalation", f"رصيد العميل تغيّر {bal_before}→{bal_after} عبر أزرار الأدمن")
    if leaked:
        ISSUES.add("high", "admin-callback-leak", f"عميل عادي تلقى ردوداً من أزرار أدمن: {leaked[:10]}")
    print(f"   جُرّب {len(admin_cds)} زر أدمن كعميل")
    print("══ 7) أزرار قديمة (stale): إعادة ضغط 120 زراً قديماً عشوائياً")
    for actor in (u.a, adm.a):
        old = [b["callback_data"] for b in actor.all_buttons if b.get("callback_data")]
        for cd in random.sample(old, min(60, len(old))):
            await actor.click(cd, label=f"🔁 قديم {cd}", expect_ok=False)
    # سلامة الدفتر المالي
    from app.db import pool as db
    rows = await db.fetch("SELECT u.tg_id, u.balance_usd, COALESCE(SUM(l.amount_usd),0) AS s FROM users u LEFT JOIN ledger l ON l.user_id=u.tg_id GROUP BY u.tg_id, u.balance_usd")
    for r in rows:
        if Decimal(r["balance_usd"]) != Decimal(r["s"]):
            ISSUES.add("critical", "ledger-mismatch", f"المستخدم {r['tg_id']}: الرصيد {r['balance_usd']} ≠ مجموع الدفتر {r['s']}", where="integrity")
        if Decimal(r["balance_usd"]) < 0:
            ISSUES.add("critical", "negative-balance", f"المستخدم {r['tg_id']} رصيده سالب {r['balance_usd']}", where="integrity")
    print("   الحالات التي زارها المستكشف:", sorted(u.states_seen | u2.states_seen | adm.states_seen))
    report(os.path.join(OUT, "crawl_issues.json"), "نتيجة الاستكشاف الآلي")
    await env.close()

if __name__ == "__main__":
    asyncio.run(main())
