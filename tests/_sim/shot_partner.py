"""لقطات شاشة حقيقية لصفحة «📡 القنوات الشريكة» داخل Cpanel (Chromium بلا واجهة) + رصد أخطاء JS.
تشغيل: source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/shot_partner.py
"""
import asyncio, os, sys, json
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", PORT="18096")
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from fakesession import FakeSession
from shot_cpanel import init_data, THEMES
from app.db import pool as db
from app.services import pricing as P, cpanel as CP
from app.web import make_app
from app.web_cpanel import setup_cpanel

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); await db.run_migrations()
    await db.execute("DELETE FROM partner_channels"); await db.execute("DELETE FROM orders WHERE kind='tg_post'")
    from app.db.repo import settings as srepo; srepo.invalidate(); await P.refresh(); await CP.refresh_runtime()
    for c in ({"title": "سوق الشام", "url": "@souq_sham", "category": "shopping", "subscribers": "45000", "avg_views": "9000", "price_24h": "12", "price_48h": "18", "allow_pin": True, "owner_contact": "@sham_owner"},
              {"title": "عروض حلب", "url": "@aleppo_offers", "category": "shopping", "subscribers": "31000", "price_24h": "8.5", "allow_pin": False},
              {"title": "تك بالعربي", "url": "t.me/tech_ar", "category": "tech", "subscribers": "22000", "price_24h": "10", "price_pin": "20"},
              {"title": "دليل المتاجر", "url": "@stores_guide", "category": "shopping", "subscribers": "18000", "price_24h": "5", "enabled": False}):
        await CP.save_partner_channel(c, admin_id=999)
    await db.execute("""INSERT INTO orders(user_id,kind,status,spec,price_usd,cost_usd,paid_at,completed_at) SELECT 555,'tg_post','completed','{"kind":"tg_post","format":"24h"}',15,12,now(),now()
                        WHERE EXISTS (SELECT 1 FROM users WHERE tg_id=555)""")
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18096).start()
    from playwright.async_api import async_playwright
    out = os.path.join(ROOT, "..", "cpanel_shots"); os.makedirs(out, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        for theme in ("light", "dark"):
            tp = THEMES[theme]
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ar", color_scheme=theme)
            css = ";".join(f"--tg-theme-{k.replace('_', '-')}:{v}" for k, v in tp.items())
            await ctx.add_init_script(f"""
                window.Telegram = {{ WebApp: {{ initData: {json.dumps(init_data())}, initDataUnsafe: {{}}, themeParams: {json.dumps(tp)}, colorScheme: {json.dumps(theme)},
                  ready(){{}}, expand(){{}}, setHeaderColor(){{}}, enableClosingConfirmation(){{}}, disableClosingConfirmation(){{}},
                  showConfirm(m, cb){{ cb(true); }}, BackButton: {{ show(){{ window.__bb = true; }}, hide(){{ window.__bb = false; }}, onClick(f){{ window.__bbf = f; }}, offClick(){{ window.__bbf = null; }} }},
                  HapticFeedback: {{ notificationOccurred(){{}} }} }} }};
                (document.documentElement ? Promise.resolve(document.documentElement) : new Promise(r => document.addEventListener("readystatechange", () => r(document.documentElement), {{once:true}}))).then(el => el.style.cssText += ";{css}");
            """)
            await ctx.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(status=200, body="", content_type="application/javascript"))
            page = await ctx.new_page(); errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto("http://127.0.0.1:18096/cpanel"); await page.wait_for_selector("#app:not(.hidden)", timeout=10000); await page.wait_for_timeout(300)
            await page.screenshot(path=f"{out}/{theme}_p0_home.png")
            await page.click('.tab[data-tab="services"]'); await page.wait_for_timeout(150)
            await page.screenshot(path=f"{out}/{theme}_p1_services.png")
            await page.click('button[onclick="openPartner()"]'); await page.wait_for_timeout(200)
            print(theme, "BackButton shown:", await page.evaluate("window.__bb"))
            await page.screenshot(path=f"{out}/{theme}_p2_list.png", full_page=True)
            if theme == "light":
                # فلترة بالفئة
                await page.click('#pc-cats .chip[data-c="tech"]'); await page.wait_for_timeout(100)
                n = await page.evaluate('document.querySelectorAll("#pc-list .pc").length'); print("tech filter →", n, "channel(s)")
                await page.click('#pc-cats .chip[data-c="all"]')
                # نموذج إضافة + معاينة حية
                await page.click('button[onclick="openPcForm(null)"]'); await page.wait_for_timeout(150)
                await page.fill('[data-pc="title"]', "أخبار دمشق"); await page.fill('[data-pc="url"]', "@damascus_news"); await page.select_option('#pc-cat-sel', "news")
                await page.fill('[data-pc="subscribers"]', "60000"); await page.fill('[data-pc="price_24h"]', "20"); await page.fill('[data-pc="price_48h"]', "30")
                await page.wait_for_timeout(100); print("preview:", await page.inner_text("#pc-prev"))
                await page.screenshot(path=f"{out}/light_p3_form.png", full_page=True)
                # خطأ تحقق من الخادم (رابط خاطئ)
                await page.fill('[data-pc="url"]', "damascus news"); await page.click("#pc-save-btn"); await page.wait_for_timeout(400)
                print("toast:", await page.inner_text("#toast"))
                await page.screenshot(path=f"{out}/light_p4_form_error.png")
                await page.fill('[data-pc="url"]', "@damascus_news"); await page.click("#pc-save-btn"); await page.wait_for_timeout(500)
                print("after save → form hidden:", await page.evaluate('document.getElementById("pc-form").classList.contains("hidden")'),
                      "| list count:", await page.evaluate('document.querySelectorAll("#pc-list .pc").length'), "| toast:", await page.inner_text("#toast"))
                await page.screenshot(path=f"{out}/light_p5_list_after_add.png", full_page=True)
                # تعديل قناة موجودة: تحميل القيم
                await page.click('#pc-list [data-edit]'); await page.wait_for_timeout(150)
                print("edit form title:", await page.inner_text("#pc-form-title"), "| price_24h:", await page.input_value('[data-pc="price_24h"]'))
                await page.evaluate("window.__bbf && window.__bbf()")  # زر الرجوع في تيليغرام يغلق النموذج
                print("back → form hidden:", await page.evaluate('document.getElementById("pc-form").classList.contains("hidden")'))
                # إيقاف ثم حذف
                await page.click('#pc-list [data-tog]'); await page.wait_for_timeout(300); print("toggle toast:", await page.inner_text("#toast"))
                dels = await page.query_selector_all('#pc-list [data-del]'); await dels[-1].click(); await page.wait_for_timeout(400); print("delete toast:", await page.inner_text("#toast"))
                await page.evaluate("window.__bbf && window.__bbf()"); await page.wait_for_timeout(150)
                print("back → page hidden:", await page.evaluate('document.getElementById("pc-page").classList.contains("hidden")'), "| BackButton:", await page.evaluate("window.__bb"))
                print("services hint:", (await page.inner_text("#services")).replace("\n", " / ")[:300])
            print(theme, "JS errors:", errors)
            await ctx.close()
        await browser.close()
    await runner.cleanup(); await db.close_pool()
    print("shots →", sorted(f for f in os.listdir(out) if "_p" in f))

asyncio.run(main())
