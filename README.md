# 🤖 روّج بوت — الخطوة 2 من 6

بوت تيليغرام لبيع خدمات الترويج (إعلانات فيسبوك/إنستغرام عبر Nour Ads، Telegram Ads، قنوات شريكة، تصميم وكتابة).
مبني بـ Python + aiogram 3، قاعدة بيانات Neon Postgres، استضافة Render (خطة مجانية)، وإبقاء بـ UptimeRobot.

> **ما الذي يعمل في هذه الخطوة؟** الترحيب والشروط، اللوحة الرئيسية بأزرارها السبعة، شاشات المعاينة لكل خدمة،
> الأسعار وكيف يعمل، الأسئلة الشائعة، عرض الرصيد، لوحة الأدمن مع تشغيل/إيقاف الخدمات، `/health` لـ UptimeRobot.
> معالجات الطلب والشحن تأتي في الخطوات 2–6.

---

## 0) ما تحتاجه قبل البدء (10 دقائق)

| | من أين | ما الذي تنسخه |
|---|---|---|
| توكن البوت | [@BotFather](https://t.me/BotFather) ← `/newbot` | `123456789:AAE...` — **ابدأ ببوت تجريبي** واحتفظ بالرسمي للنهاية |
| معرّفك الرقمي | أرسل `/id` لأي بوت يعمل من هذا الكود، أو [@userinfobot](https://t.me/userinfobot) | رقم مثل `123456789` |
| قاعدة البيانات | [neon.tech](https://neon.tech) ← New Project | زر **Connect** ← اختر **Pooled connection** ← انسخ الرابط كاملاً |
| حساب GitHub | [github.com](https://github.com) | مستودع **خاص** (Private) باسم `promo-bot` |
| حساب Render | [render.com](https://render.com) — سجّل بحساب GitHub | لا يحتاج بطاقة |

---

## 1) رفع الكود إلى GitHub

**الطريقة السهلة (بدون أوامر):** أنشئ المستودع الخاص على GitHub ← زر **Add file ← Upload files** ← اسحب محتويات مجلد `promo-bot` كاملاً (المجلدات `app` و`tests` والملفات `requirements.txt` `render.yaml` `.env.example` `.gitignore` `README.md`) ← **Commit**.

**بالأوامر (ويندوز PowerShell أو ماك/لينكس):**
```bash
cd promo-bot
git init
git add .
git commit -m "step 1: skeleton, db, main menu"
git branch -M main
git remote add origin https://github.com/<اسمك>/promo-bot.git
git push -u origin main
```

> ⚠️ لا ترفع ملف `.env` أبداً (هو في `.gitignore` أصلاً). الأسرار تُدخل في Render فقط.

---

## 2) إنشاء الخدمة على Render

1. **New ← Web Service** ← اختر مستودع `promo-bot`.
2. املأ:
   - **Name:** `promo-bot` (سيصبح رابطك `https://promo-bot.onrender.com` — أو أي اسم متاح)
   - **Region:** Frankfurt (الأقرب)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m app.main`
   - **Instance Type:** **Free**
3. **Environment ← Add Environment Variable** — أضف هذه بالضبط:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | توكن BotFather |
   | `DATABASE_URL` | رابط Neon المجمّع (Pooled) كاملاً |
   | `ADMIN_IDS` | معرّفك الرقمي (وأكثر من واحد بفواصل: `111,222`) |
   | `MODE` | `webhook` |
   | `WEBHOOK_SECRET` | سلسلة عشوائية طويلة من أحرف وأرقام فقط، مثل `k7Qp2ZxW9mL4tR8vB3nH6yJ1` |
   | `TZ` | `Asia/Damascus` |
   | `NOUR_DRY_RUN` | `1` |
   | `SUPPORT_USERNAME` | يوزر حساب الدعم بدون @ (اختياري) |

   > لا تحتاج `PUBLIC_URL`: Render يوفّر الرابط تلقائياً في `RENDER_EXTERNAL_URL` والكود يقرؤه.
   > (بديل: **New ← Blueprint** يقرأ `render.yaml` وينشئ كل شيء، ثم تدخل الأسرار الثلاثة فقط.)

4. **Create Web Service** ← انتظر 2–3 دقائق حتى ترى في السجل (Logs):
   ```
   INFO db: Postgres pool ready
   INFO db: migration applied: 001_init.sql
   INFO main: webhook set: https://promo-bot.onrender.com/webhook/...
   INFO main: bot @اسم_البوت started — v0.2.1 step 1/6 mode=webhook
   ```
5. ستصلك رسالة على تيليغرام من البوت: **«✅ روّج بوت انطلق»** ← افتح البوت واضغط **Start** 🎉
   (إذا لم تصل الرسالة الأولى: اضغط Start في البوت أولاً ثم أعد النشر — تيليغرام لا يسمح للبوت بمراسلتك قبل أن تراسله.)

---

## 3) إبقاء البوت مستيقظاً — UptimeRobot (دقيقتان)

الخطة المجانية في Render تُنيم الخدمة بعد 15 دقيقة بلا زيارات، فيتأخر أول ردّ نصف دقيقة.

1. [uptimerobot.com](https://uptimerobot.com) ← **New Monitor**
2. Type: **HTTP(s)** · URL: `https://promo-bot.onrender.com/health` · Interval: **5 minutes**
3. أضف بريدك للتنبيه إن سقط البوت.

`/health` لا يلمس قاعدة البيانات عمداً — حتى تنام Neon وتبقى ضمن 100 ساعة-حوسبة الشهرية.

---

## 4) التشغيل على جهازك (اختياري — للتجربة قبل النشر)

```bash
cd promo-bot
python -m venv .venv
# ويندوز:  .venv\Scripts\activate      ماك/لينكس:  source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env      # ماك/لينكس: cp .env.example .env
# افتح .env واملأ BOT_TOKEN و DATABASE_URL و ADMIN_IDS  (MODE=polling)
python -m app.main
```
> استخدم **بوتاً ثانياً** (dev) للتجربة المحلية — البوت الواحد لا يعمل بوضعَي polling وwebhook معاً.
> ولقاعدة معزولة: في Neon أنشئ **Branch** باسم `dev` وضع رابطها في `.env`.

الاختبارات (بلا شبكة ولا قاعدة):
```bash
pip install pytest
python -m pytest -q
```

---

## 5) هيكل المشروع

```
promo-bot/
├── app/
│   ├── main.py               # الإقلاع: polling محلياً / webhook على Render + خادم /health
│   ├── config.py             # قراءة متغيرات البيئة والتحقق منها برسائل واضحة
│   ├── web.py                # /  و /health (بلا DB) و مسار الويبهوك
│   ├── db/
│   │   ├── pool.py           # اتصال Neon الصغير + إعادة محاولة + الترحيلات التلقائية
│   │   ├── migrations/001_init.sql   # 12 جدولاً: users, ledger, topups, orders, tasks, channels, tickets, settings, fsm_state, events…
│   │   └── repo/             # users.py, settings.py, events.py
│   ├── bot/
│   │   ├── texts.py          # كل النصوص العربية
│   │   ├── keyboards.py      # كل الأزرار — مطابقة لخريطة الأزرار
│   │   ├── fsm_storage.py    # حالة المعالج في Postgres (تصمد أمام إعادة التشغيل)
│   │   ├── middlewares.py    # تسجيل المستخدم، الحظر، التقاط الأخطاء
│   │   └── handlers/         # start.py (H0/H1) · menu.py (M0 T0 D0 B0 O0 S0 I0) · admin/panel.py (A0) · fallback.py
│   └── services/pricing.py   # جدول الأسعار — مصدر واحد للحقيقة
├── tests/                    # اختبارات التسعير والإعدادات
├── requirements.txt · render.yaml · .env.example · .gitignore
```

---

## 6) أوامر البوت

| للمستخدم | للأدمن |
|---|---|
| `/start` القائمة · `/balance` رصيدي · `/orders` طلباتي · `/help` الدعم · `/cancel` إلغاء · `/id` معرّفي | `/admin` لوحة الإدارة (أو زر 🛠️ في القائمة) |

---

## 7) مشاكل شائعة

| العرض | السبب والحل |
|---|---|
| السجل يقول `❌ لا يمكن تشغيل البوت — أصلح متغيرات البيئة` | متغير ناقص — الرسالة تسمّيه بالضبط |
| `تيليغرام رفض التوكن` | انسخ التوكن كاملاً من BotFather بلا مسافات |
| `تعذّر الاتصال بقاعدة البيانات` | استخدم رابط **Pooled** من Neon، وتأكد أن المشروع غير متوقف (Neon يوقف المشاريع الخاملة أسابيع) |
| البوت لا يرد أبداً على Render | تأكد `MODE=webhook` و`WEBHOOK_SECRET` موجود؛ افتح `https://promo-bot.onrender.com/health` — إن لم تفتح فالخدمة نائمة أو فشل البناء |
| يرد بعد 30 ثانية من أول رسالة | الخدمة كانت نائمة — فعّل UptimeRobot |
| رسالة `Conflict: terminated by other getUpdates` محلياً | نسختان تعملان polling بنفس التوكن — أغلق إحداهما |

---

## الخطوات القادمة

2. الرصيد والشحن (USDT + الطرق المحلية + اعتماد الأدمن)
3. معالج إعلان Meta كاملاً + التسعير
4. عميل Nour Ads API + المزامنة والمصالحة
5. تيليغرام (Telegram Ads + القنوات الشريكة) + المجدول والإشعارات
6. التصميم والكتابة، التذاكر، الإحصائيات، البث

---

<p align="center"><sub>صُمِّم وبُني بإبداع مع <b>Arena.ai</b> ✨</sub></p>


## 🆕 ما الجديد في الخطوة 2 (v0.2.1) — الرصيد والشحن

- 💰 **رصيدي**: الرصيد الحالي + آخر عملية + سجل العمليات (صفحات).
- ➕ **شحن رصيد بـ USDT** على شبكتي **TRC20** و **BEP20** فقط — الحد الأدنى 5$.
- بطاقة تعليمات لكل طلب برقم `#TOP-xx` وعنوان قابل للنسخ بضغطة، ثم إثبات (صورة أو TxID).
- 📥 **الأدمن** يستلم بطاقة لكل طلب: ✅ اعتماد / ❌ رفض (أسباب جاهزة) / ✏️ اعتماد بمبلغ مختلف / 💬 مراسلة.
- الاعتماد = معاملة واحدة (رصيد + دفتر `ledger` + حالة الطلب) مع قفل صف — لا يمكن اعتماد الطلب مرتين ولو ضغط أدمنان معاً.
- 🏦 **عناوين المحافظ تُدار من البوت**: 🛠️ لوحة الإدارة → ⚙️ إعدادات → 🏦 طرق الدفع → اختر الشبكة → ✏️ تعديل العنوان.
  **الطريقة لا تظهر للعملاء إلا إذا كانت مفعّلة ولها عنوان.**
- الترحيل `002_topups.sql` يُطبَّق تلقائياً عند الإقلاع (لا حاجة لأي أمر يدوي على Neon).

### تجربة سريعة بعد النشر
1. من حساب الأدمن: `/admin` → ⚙️ إعدادات → 🏦 طرق الدفع → TRC20 → ✏️ تعديل العنوان → ألصق عنوانك.
2. من أي حساب عميل: 💰 رصيدي → ➕ شحن رصيد → TRC20 → 5$ → ✅ حوّلت → أرسل أي صورة.
3. تصلك بطاقة الطلب كأدمن → ✅ اعتماد → يصل العميل «تم شحن 5$» ويظهر الرصيد في 💰 رصيدي.
