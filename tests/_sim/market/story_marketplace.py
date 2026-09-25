"""قصة سوق القنوات كما يراها كل شخص في تيليغرام — تُكتب في STORY_marketplace.md.

الشخصيات: أبو أحمد (صاحب قناة، مستخدم عادي) · رأفت (أدمن البوت) · سامر (عميل) · ورأفت نفسه كصاحب قناة.
كل رسالة وزر في الملف خرجت فعلاً من كود البوت الحقيقي (لا نص مكتوب يدوياً).
تشغيل: /tmp/freshdb.sh && PYTHONPATH=. DATABASE_URL=... python tests/_sim/market/story_marketplace.py
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "explore"))
import sim_marketplace as S  # noqa: E402
from harness import *  # noqa: E402,F403
from harness import _kb_buttons, A, U  # noqa: E402
from crawler import seed  # noqa: E402
from scenarios import drive  # noqa: E402

OWNER = 700
NAMES = {OWNER: "📱 أبو أحمد (صاحب القناة)", A: "🛠️ رأفت (أدمن البوت)", U: "🛒 سامر (العميل)"}
CH_NAME = {S.CH_MAIN: "📢 قناة «عروض دمشق»", S.CH_ADM1: "📢 قناة الأدمن «متجر الأدمن»"}
MD: list[str] = []
SEEN_KB: set = set()


def clean(t: str) -> str:
    t = (t or "").replace("\u2800", "").replace("\u200b", "")
    t = re.sub(r"</?(b|strong)>", "**", t)
    t = re.sub(r"</?(i|em)>", "_", t)
    t = re.sub(r"</?code>", "`", t)
    t = re.sub(r"<a href=\"([^\"]+)\">([^<]+)</a>", r"\2 (\1)", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def head(t):
    MD.append(f"\n## {t}\n")


def note(t):
    MD.append(f"\n> 💬 {t}\n")


def show(action: str, who: int, out, extra_chats=()):
    """يعرض ما رآه كل طرف بعد خطوة: الأطراف الثلاثة + القنوات."""
    MD.append(f"\n**{NAMES.get(who, who)} ← {action}**\n")
    targets = list(NAMES) + list(extra_chats)
    for n, d in out:
        cid = d.get("chat_id")
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        if n == "AnswerCallbackQuery":
            continue
        if cid not in targets:
            continue
        text = clean(d.get("text") or d.get("caption") or "")
        if n == "SendMediaGroup":
            text = f"[ألبوم {len(d.get('media', []))} صور] " + clean((d.get("media") or [{}])[0].get("caption") or "")
        if n in ("DeleteMessage",):
            text = f"🗑️ حذف الرسالة {d.get('message_id')}"
        elif n == "PinChatMessage":
            text = f"📌 تثبيت الرسالة {d.get('message_id')}"
        elif n == "UnpinChatMessage":
            text = f"📍 فك تثبيت الرسالة {d.get('message_id')}"
        elif n in ("EditMessageReplyMarkup", "GetChatMember", "GetChat", "GetChatMemberCount"):
            continue
        if not text:
            continue
        who_lbl = NAMES.get(cid) or CH_NAME.get(cid) or f"قناة {cid}"
        verb = "✏️ (تعديل نفس الرسالة)" if n.startswith("Edit") else ""
        MD.append(f"- يرى **{who_lbl}** {verb}:\n")
        MD.append("  ```\n  " + text.replace("\n", "\n  ") + "\n  ```")
        btns = _kb_buttons(d.get("reply_markup"))
        if btns:
            rows = (d.get("reply_markup") or {}).get("inline_keyboard") or []
            MD.append("  أزرار: " + " ⏐ ".join("  ".join(f"[{b.get('text')}]" for b in row) for row in rows))
    for n, d in out:
        if n == "AnswerCallbackQuery" and d.get("text"):
            MD.append(f"- 🔔 تنبيه منبثق: «{clean(d['text'])}»")


async def main():
    env = await Env(verbose=False).boot()  # noqa: F405
    from app.bot.wide import WideButtonsMiddleware
    env.bot.session = S.MarketSession()
    env.bot.session.middleware(WideButtonsMiddleware())
    await seed(env)
    from app.db.repo import users as users_repo
    from app.services import money, marketplace as MP
    await users_repo.upsert_user(OWNER, "أبو أحمد", "abu_ahmad")
    await money.credit(U, Decimal("100"), "topup", note="seed")
    ow, a, u = env.actor(OWNER, "أبو أحمد"), env.actor(A, "رأفت"), env.actor(U, "سامر")

    MD.append("# 💼 قصة سوق القنوات كما يراها كل شخص\n")
    MD.append("كل رسالة وزر هنا خرجت من كود البوت الحقيقي أثناء محاكاة كاملة (تيليغرام وهمي يحاكي القنوات).\n")

    # ═══ 1 ═══
    head("1) أبو أحمد عنده قناة ويريد أن يربح منها (مستخدم عادي)")
    out = await ow.text("/start"); out += await ow.click("nav:accept")
    show("يفتح البوت أول مرة ويوافق على الشروط", OWNER, out[-3:])
    out = await ow.click("mp:home"); show("يضغط «💼 اربح من قناتك»", OWNER, out)
    out = await ow.click("mp:how"); show("يضغط «❓ كيف يعمل والشروط»", OWNER, out)
    out = await ow.click("mp:add"); show("يضغط «➕ سجّل قناتي»", OWNER, out)
    note("يضغط «➕ أضف البوت لقناتي» ← تيليغرام يفتح اختيار القناة مع الصلاحيات جاهزة ← يختار «عروض دمشق» ويوافق. "
         "تيليغرام يرسل للبوت حدث «أُضفت مشرفاً»:")
    out = await S.chat_member_update(env, S.CH_MAIN, OWNER, False, True)
    show("(تلقائياً بعد إضافة البوت للقناة)", OWNER, out)
    cid = (await MP.channel_by_chat(S.CH_MAIN))["id"]
    out = await ow.click(f"mp:cat:{cid}:shopping"); show("يختار الفئة «🛍️ تسوق ومتاجر»", OWNER, out)
    out = await ow.text("10"); show("يكتب سعر منشور 24 ساعة: 10", OWNER, out)
    out = await ow.text("15"); show("يكتب سعر 48 ساعة: 15", OWNER, out)
    out = await ow.text("14"); show("يكتب سعر المثبّت: 14", OWNER, out)
    out = await ow.text("أقوى عروض المحلات في دمشق يومياً"); show("يكتب وصفاً قصيراً", OWNER, out)
    out = await ow.press("أوافق وأرسل"); show("يضغط «أوافق وأرسل للمراجعة»", OWNER, out)

    # ═══ 2 ═══
    head("2) رأفت (أدمن البوت) يراجع القناة ويعتمدها")
    note("بطاقة القناة وصلت للأدمن فور الإرسال (ظهرت أعلاه). رأفت يستطيع الاعتماد من البطاقة مباشرة، أو من اللوحة:")
    out = await a.click("adm:panel"); show("يفتح 🛠️ لوحة الأدمن", A, out)
    out = await a.click("adm:mp"); show("يضغط «💼 سوق القنوات»", A, out)
    out = await a.click("adm:mp:list:pending"); show("يضغط «🕐 بانتظار الموافقة»", A, out)
    out = await a.click(f"adm:mp:ch:{cid}"); show("يفتح القناة", A, out)
    out = await a.click(f"adm:mp:ok:{cid}"); show("يضغط «✅ اعتماد»", A, out)

    # ═══ 3 ═══
    head("3) سامر (عميل) يشتري منشوراً في القناة")
    await drive(u, ["t:/start", "cb:nav:accept", "cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all"])
    out = await u.click(f"tgp:ch:{cid}"); show("يتصفح القنوات ويفتح «عروض دمشق»", U, out)
    await drive(u, [f"cb:tgp:pick:{cid}", "cb:tgp:fmt:24h", "t:🔥 تخفيضات نهاية الموسم عند محلات النور — خصم 40% على كل شي!",
                    "cb:tgp:content:next", "cb:tgp:when:asap"])
    out = await u.click("tgp:confirm")
    show("يختار 24 ساعة، يكتب النص، ويؤكد الدفع", U, out)
    oid = (await S.qv("SELECT max(id) FROM orders WHERE user_id=$1", U))
    note("لاحظ: أبو أحمد يرى **ربحه الصافي فقط (10$)**، والعميل دفع 12.50$ — الفرق عمولة البوت ولا يظهر لصاحب القناة.")

    # ═══ 4 ═══
    head("4) أبو أحمد يقبل ← البوت ينشر تلقائياً")
    out = await ow.click(f"mp:o:{oid}:acc"); show("يضغط «✅ قبول واختيار الموعد»", OWNER, out)
    out = await ow.click(f"mp:o:{oid}:t:now"); show("يضغط «⚡ الآن»", OWNER, out, extra_chats=[S.CH_MAIN])
    note("بعد 24 ساعة يحذف البوت المنشور تلقائياً (نسرّع الزمن في المحاكاة):")
    await S.age(oid, ends_at="1 minute")
    out = await S.tick(env); show("(تلقائياً عند انتهاء المدة)", OWNER, out, extra_chats=[S.CH_MAIN])
    out = await u.click(f"mpc:rate:{oid}:5"); show("سامر يقيّم ⭐⭐⭐⭐⭐", U, out)
    note("بعد 48 ساعة بلا بلاغ من العميل يتحرر الربح:")
    await S.age(oid, payout_at="1 minute")
    out = await S.tick(env); show("(تلقائياً بعد فترة الحجز)", OWNER, out)

    # ═══ 5 ═══
    head("5) أبو أحمد يسحب أرباحه ← رأفت يحوّل")
    from app.db import pool as db
    await db.execute("UPDATE users SET earn_avail_usd = earn_avail_usd + 5 WHERE tg_id=$1", OWNER)
    await db.execute("INSERT INTO earnings_ledger (user_id,bucket,type,amount_usd,note) VALUES ($1,'avail','adjust',5,'story')", OWNER)
    note("(أضفنا 5$ من طلب سابق حتى يتجاوز الحد الأدنى 10$)")
    out = await ow.click("mp:earn"); show("يضغط «💰 أرباحي»", OWNER, out)
    out = await ow.click("mp:wd"); show("يضغط «💸 سحب الأرباح»", OWNER, out)
    out = await ow.click("mp:wd:m:shamcash"); show("يختار شام كاش", OWNER, out)
    out = await ow.text("0933123456"); show("يكتب رقم حسابه", OWNER, out)
    out = await ow.click("mp:wd:ok"); show("يضغط «✅ تأكيد السحب»", OWNER, out)
    pid = await S.qv("SELECT max(id) FROM payouts")
    out = await a.click(f"adm:mp:paid:{pid}"); show("رأفت يحوّل المبلغ ثم يضغط «✅ تم الدفع»", A, out)
    out = await a.text("SC-778812"); show("يكتب رقم العملية", A, out)

    # ═══ 6 ═══
    head("6) رأفت نفسه عنده قناة (الحالة التي ظهرت في التجربة الحقيقية)")
    note("رأفت أضاف البوت لقناته **بدون** المرور بزر «اربح من قناتك»:")
    out = await S.chat_member_update(env, S.CH_ADM1, A, False, True)
    show("(تلقائياً بعد إضافة البوت)", A, out)
    out = await a.click(f"mp:reg:{S.CH_ADM1}"); show("يضغط «💼 اعرضها في سوق القنوات»", A, out)

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "STORY_marketplace.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(MD) + "\n")
    print("كُتب", path, "—", len(MD), "سطر")
    serious = [i for i in ISSUES.items if i.sev in ("critical", "high")]  # noqa: F405
    for i in serious:
        print("⚠️", i.sev, i.kind, i.detail[:150])
    await env.close()


if __name__ == "__main__":
    asyncio.run(main())
