"""محاكاة كاملة عبر البوت: عميل بلا @username يطلب إعلان Meta (نور يشترط telegram_username).

أ) لا معرّف احتياطي ← لا زر «متابعة بدون معرّف»، الزر القديم والتأكيد المباشر مرفوضان، ولا خصم.
ب) الأدمن يضبط المعرّف الاحتياطي من البوت ← يظهر زر المتابعة، الطلب يُرسل لنور بمعرّف الأدمن.
ج) طلب مدفوع علق بلا معرّف ← ينتظر بلا استرداد، وضبط المعرّف يوقظه فيُرسل.
تشغيل: /tmp/freshdb.sh && /tmp/venv/bin/python tests/_sim/review3/sim_no_username.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "explore"))
from harness import *  # noqa: E402,F403
from crawler import seed  # noqa: E402
from scenarios import drive, bal, qv, q1, expect, meta_to_summary  # noqa: E402

from decimal import Decimal  # noqa: E402
D = Decimal
ALERT = "اضبط معرّف تيليغرام"


def alerts(out):
    return " ".join((d.get("text") or "") for n, d in out if n == "AnswerCallbackQuery")


async def main():
    env = await Env(verbose=False).boot()  # noqa: F405
    await seed(env)
    from app.db.repo import settings as S
    from app.services import money, scheduler, orders as O
    await S.set_("admin_fallback_username", "")
    await money.credit(U2, D("100"), "topup", note="seed")  # noqa: F405
    u, a = env.actor(U2, "بلا-معرّف"), env.actor(A, "أدمن")  # noqa: F405
    await drive(u, ["t:/start", "cb:nav:accept"])
    b0 = await bal(U2)  # noqa: F405

    print("══ أ) لا معرّف احتياطي")
    await meta_to_summary(u, False)
    txt = " ".join(u.last_texts)
    expect("ما إله معرّف" in txt, "high", "no-uname-screen", f"لم تظهر شاشة المعرّف: {txt[:150]}")
    expect(not u.find_cb("meta:uname_skip"), "critical", "skip-shown", "زر «متابعة بدون معرّف» ظاهر رغم عدم ضبط المعرّف الاحتياطي")
    expect("مطلوب" in txt, "medium", "required-line", "لا يوضح أن المعرّف مطلوب")
    out = await u.click("meta:uname_skip", label="زر تخطٍّ قديم", expect_ok=False)
    expect(ALERT in alerts(out), "high", "stale-skip", f"الزر القديم لم يُرفض بتنبيه: {alerts(out)[:120]}")
    expect(not u.find_cb("meta:confirm"), "critical", "stale-skip-summary", "الزر القديم أوصل لشاشة التأكيد")
    out = await u.click("meta:confirm", label="تأكيد مباشر بلا معرّف", expect_ok=False)
    n0 = await qv("SELECT count(*) FROM orders WHERE user_id=$1 AND kind='meta_campaign' AND status<>'awaiting_payment'", U2)  # noqa: F405
    expect(await bal(U2) == b0 and n0 == 0, "critical", "charged-no-uname",  # noqa: F405
           f"خُصم/أُنشئ طلب بلا معرّف: الرصيد {b0}→{await bal(U2)} طلبات={n0}")  # noqa: F405
    expect(ALERT in alerts(out), "high", "confirm-alert", "التأكيد بلا معرّف لم يوضح السبب")
    print(f"    الرصيد ثابت {await bal(U2)} · طلبات {n0} · تنبيه: {alerts(out)[:60]}")  # noqa: F405

    print("══ ب) الأدمن يضبط المعرّف الاحتياطي من البوت")
    await drive(a, ["cb:adm:fallback", "t:@tarweej_admin"])
    expect(await O.fallback_username() == "tarweej_admin", "high", "fallback-save", "لم يُحفظ المعرّف الاحتياطي")
    await u.click("meta:wa_ok")
    expect(u.find_cb("meta:uname_skip") is not None, "high", "skip-missing", "زر المتابعة لم يظهر بعد ضبط المعرّف الاحتياطي")
    await drive(u, ["cb:meta:uname_skip"])
    expect("يتواصل معك فريقنا" in " ".join(u.last_texts), "low", "summary-tg", "سطر المعرّف في الملخص")
    price_btn = u.find_cb("meta:confirm")
    expect(price_btn is not None, "critical", "no-confirm", "لا زر تأكيد بعد المتابعة")
    await u.click("meta:confirm")
    o = await q1("SELECT id, status, price_usd, nour_payload->>'telegram_username' AS tg FROM orders "  # noqa: F405
                 "WHERE user_id=$1 AND kind='meta_campaign' ORDER BY id DESC LIMIT 1", U2)  # noqa: F405
    expect(o and o["status"] == "submitted", "critical", "not-submitted", f"الطلب لم يُرسل: {dict(o) if o else None}")
    expect(o and o["tg"] == "tarweej_admin", "critical", "payload-tg", f"أُرسل لنور telegram_username={o and o['tg']!r}")
    expect(b0 - await bal(U2) == (o["price_usd"] if o else 0), "critical", "charge", "الخصم لا يطابق السعر")  # noqa: F405
    print(f"    ORD-{o['id']} {o['status']} · نور استلم telegram_username={o['tg']!r} · خُصم {o['price_usd']}")

    print("══ ج) طلب مدفوع علق بلا معرّف ← ينتظر ثم يستيقظ")
    await S.set_("admin_fallback_username", "")
    spec = (await q1("SELECT spec FROM orders WHERE id=$1", o["id"]))["spec"]  # noqa: F405
    import json
    spec = json.loads(spec) if isinstance(spec, str) else spec
    spec["tg_username"] = None
    b1 = await bal(U2)  # noqa: F405
    o2 = await O.confirm(U2, spec, checkout_key=f"nu-wait-{o['id']}")  # noqa: F405
    x = await O.submit(o2["id"])
    expect(x["status"] == "paid" and "المعرّف الاحتياطي" in (x.get("note") or ""), "critical", "wait",
           f"الطلب لم ينتظر: {x['status']} {x.get('note')}")
    expect(await bal(U2) == b1 - o2["price_usd"], "critical", "wait-refund", "تغيّر الرصيد أثناء الانتظار")  # noqa: F405
    await drive(a, ["cb:adm:fallback", "t:tarweej_admin"])
    expect("كان ينتظر" in " ".join(a.last_texts), "medium", "woken-msg", f"لم يُبلَّغ الأدمن بإيقاظ الطلبات: {a.last_texts[-1][:120] if a.last_texts else ''}")
    await scheduler._retry_submissions(env.bot)
    y = await q1("SELECT status, nour_payload->>'telegram_username' AS tg FROM orders WHERE id=$1", o2["id"])  # noqa: F405
    expect(y["status"] == "submitted" and y["tg"] == "tarweej_admin", "critical", "wake", f"بعد الضبط: {dict(y)}")
    print(f"    ORD-{o2['id']}: انتظر مدفوعاً ← بعد ضبط المعرّف: {y['status']} ({y['tg']})")

    print("══ د) عميل بمعرّف: لا تغيير")
    await S.set_("admin_fallback_username", "")
    uu = env.actor(U, "بمعرّف")  # noqa: F405
    await meta_to_summary(uu, False)
    expect(uu.find_cb("meta:confirm") is not None, "critical", "with-uname", "عميل بمعرّف لم يصل للتأكيد مباشرة")
    await uu.click("meta:confirm")
    z = await q1("SELECT status, nour_payload->>'telegram_username' AS tg FROM orders WHERE user_id=$1 ORDER BY id DESC LIMIT 1", U)  # noqa: F405
    expect(z["status"] == "submitted" and z["tg"] == "samer", "critical", "own-uname", f"{dict(z)}")
    print(f"    عميل @samer: {z['status']} · telegram_username={z['tg']!r}")

    bad = [i for i in ISSUES.items if i.kind not in ("double-answer",)]  # noqa: F405
    print(f"\n══ النتيجة: {len(bad)} مشكلة")
    for i in bad:
        print("  ", i.sev, i.kind, i.detail[:160])
    await env.close()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
