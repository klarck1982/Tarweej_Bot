"""لقطات شاشة حقيقية لـ Cpanel (Chromium بلا واجهة) مع محاكاة كائن Telegram.WebApp + initData موقّعة.
تشغيل: source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/shot_cpanel.py
"""
import asyncio, os, sys, json, hmac, hashlib, time
from urllib.parse import urlencode
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", PORT="18098")
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from fakesession import FakeSession
from app.db import pool as db
from app.services import pricing as P, cpanel as CP
from app.web import make_app
from app.web_cpanel import setup_cpanel

def init_data(uid=999):
    params = {"auth_date": str(int(time.time())), "query_id": "AAH", "user": json.dumps({"id": uid, "first_name": "رأفت"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", b"123456:TESTTOKEN", hashlib.sha256).digest()
    return urlencode({**params, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})

THEMES = {
    "light": {"bg_color": "#ffffff", "secondary_bg_color": "#efeff4", "text_color": "#000000", "hint_color": "#8e8e93", "link_color": "#3390ec",
              "button_color": "#3390ec", "button_text_color": "#ffffff", "section_bg_color": "#ffffff", "section_separator_color": "#e5e5ea"},
    "dark": {"bg_color": "#17212b", "secondary_bg_color": "#0e1621", "text_color": "#f5f5f5", "hint_color": "#708499", "link_color": "#6ab3f3",
             "button_color": "#5288c1", "button_text_color": "#ffffff", "section_bg_color": "#232e3c", "section_separator_color": "#2f3b4b"},
}

async def main():
    await db.init_pool(os.environ["DATABASE_URL"])
    await db.run_migrations()
    await db.execute("DELETE FROM settings WHERE key IN ('pricing','maintenance','disabled_style')")
    from app.db.repo import settings as srepo; srepo.invalidate()
    await P.refresh(); await CP.refresh_runtime()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18098).start()
    from playwright.async_api import async_playwright
    out = os.path.join(ROOT, "..", "cpanel_shots"); os.makedirs(out, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(env={**os.environ, "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", "")})
        for theme in ("light", "dark"):
            tp = THEMES[theme]
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ar", color_scheme=theme)
            css = ";".join(f"--tg-theme-{k.replace('_', '-')}:{v}" for k, v in tp.items())
            await ctx.add_init_script(f"""
                window.Telegram = {{ WebApp: {{ initData: {json.dumps(init_data())}, initDataUnsafe: {{}}, themeParams: {json.dumps(tp)}, colorScheme: {json.dumps(theme)},
                  ready(){{}}, expand(){{}}, setHeaderColor(){{}}, enableClosingConfirmation(){{}}, disableClosingConfirmation(){{}},
                  HapticFeedback: {{ notificationOccurred(){{}} }} }} }};
                (document.documentElement ? Promise.resolve(document.documentElement) : new Promise(r => document.addEventListener("readystatechange", () => r(document.documentElement), {{once:true}}))).then(el => el.style.cssText += ";{css}");
            """)
            await ctx.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(status=200, body="", content_type="application/javascript"))
            page = await ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto("http://127.0.0.1:18098/cpanel")
            await page.wait_for_selector("#app:not(.hidden)", timeout=10000)
            await page.wait_for_timeout(300)
            await page.screenshot(path=f"{out}/{theme}_1_home.png", full_page=False)
            for tab, name in (("pricing", "2_pricing"), ("pay", "3_pay"), ("services", "4_services"), ("more", "5_more")):
                await page.click(f'.tab[data-tab="{tab}"]'); await page.wait_for_timeout(150)
                await page.screenshot(path=f"{out}/{theme}_{name}.png", full_page=(tab == "pricing"))
            if theme == "light":
                # تعديل حي: مضاعف تيليغرام + → معاينة + شريط الحفظ
                await page.click('.tab[data-tab="pricing"]')
                await page.click('button[onclick="stepv(\'tg_ads.mult\',0.05)"]')
                await page.wait_for_timeout(150)
                el = await page.query_selector("#tg-prev"); await el.scroll_into_view_if_needed()
                await page.screenshot(path=f"{out}/light_6_pricing_edit.png")
                await page.click("#btn-save"); await page.wait_for_timeout(800)
                await page.screenshot(path=f"{out}/light_7_saved.png")
                print("after save: TG_ADS_MULT =", P.TG_ADS_MULT)
            print(theme, "errors:", errors)
            await ctx.close()
        # بوابة بلا تيليغرام
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        await ctx.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(status=200, body="", content_type="application/javascript"))
        page = await ctx.new_page(); await page.goto("http://127.0.0.1:18098/cpanel"); await page.wait_for_timeout(300)
        await page.screenshot(path=f"{out}/gate_browser.png"); await ctx.close()
        await browser.close()
    await runner.cleanup()
    await db.execute("DELETE FROM settings WHERE key IN ('pricing')")
    await db.close_pool()
    print("shots →", os.path.abspath(out), sorted(os.listdir(out)))

asyncio.run(main())
