"""خادم HTTP الصغير: /health (بلا قاعدة بيانات!) + /webhook/<secret> + صفحة رئيسية بسيطة.

/health هو ما يطرقه UptimeRobot كل 5 دقائق — يجب ألا يلمس Neon حتى تنام القاعدة معظم الوقت.
"""

from __future__ import annotations

import time

from aiohttp import web

from app import STEP, VERSION
from app.config import settings

_started = time.time()

_LANDING = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2AABEE);
font-family:"Segoe UI",Tahoma,Arial,sans-serif;color:#fff}}
.card{{background:rgba(255,255,255,.08);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.18);border-radius:20px;
padding:36px 40px;text-align:center;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.35)}}
h1{{margin:0 0 6px;font-size:30px}} p{{margin:6px 0;opacity:.85}} .ok{{display:inline-block;margin-top:14px;padding:6px 14px;
border-radius:999px;background:#16a34a;font-weight:700}} small{{display:block;margin-top:18px;opacity:.6}}
</style></head><body><div class="card"><h1>🤖 {name}</h1><p>بوت الترويج يعمل الآن</p>
<span class="ok">● متصل</span><p style="margin-top:14px">الإصدار {version} · الخطوة {step}</p>
<small>Crafted with Arena.ai ✨</small></div></body></html>"""


async def health(_: web.Request) -> web.Response:
    # لا DB هنا — عمداً
    return web.json_response({
        "ok": True,
        "version": VERSION,
        "step": STEP,
        "mode": settings.mode,
        "uptime_s": int(time.time() - _started),
    })


async def index(_: web.Request) -> web.Response:
    return web.Response(text=_LANDING.format(name=settings.bot_name, version=VERSION, step=STEP),
                        content_type="text/html", charset="utf-8")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)  # aiohttp يرد على HEAD تلقائياً (UptimeRobot)
    return app
