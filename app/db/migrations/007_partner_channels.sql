-- 007_partner_channels.sql — v0.6.0: القنوات الشريكة (📝 نشر في قنوات شريكة)
-- إضافات فقط — آمنة للتكرار.

CREATE TABLE IF NOT EXISTS partner_channels (
    id             BIGSERIAL PRIMARY KEY,
    title          TEXT NOT NULL,                       -- الاسم المعروض للعميل
    username       TEXT,                                -- بدون @ (قد يكون NULL لقناة خاصة برابط دعوة)
    url            TEXT NOT NULL,                       -- t.me/... موحّد
    category       TEXT NOT NULL DEFAULT 'general',     -- shopping | tech | news | fun | edu | local | general
    subscribers    INT  NOT NULL DEFAULT 0,             -- تقريبي
    avg_views      INT,                                 -- متوسط مشاهدات المنشور (اختياري)
    blurb          TEXT NOT NULL DEFAULT '',            -- وصف قصير يراه العميل
    price_24h      NUMERIC(10,2) NOT NULL,              -- ما ندفعه لصاحب القناة — منشور 24 ساعة
    price_48h      NUMERIC(10,2),                       -- NULL = الصيغة غير متاحة
    price_pin      NUMERIC(10,2),                       -- NULL = 24 ساعة + pin_extra تلقائياً (إن سُمح بالتثبيت)
    allow_pin      BOOLEAN NOT NULL DEFAULT TRUE,
    owner_contact  TEXT NOT NULL DEFAULT '',            -- خاص بالأدمن (لا يراه العميل)
    notes          TEXT NOT NULL DEFAULT '',            -- خاص بالأدمن
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    archived       BOOLEAN NOT NULL DEFAULT FALSE,      -- حذف مع وجود طلبات سابقة = أرشفة
    sort_order     INT NOT NULL DEFAULT 100,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS partner_channels_live_idx ON partner_channels(enabled, archived, category);

-- أعمدة الطلب الخاصة بالنشر
ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;   -- الموعد المؤكَّد مع القناة
ALTER TABLE orders ADD COLUMN IF NOT EXISTS post_url     TEXT;          -- رابط المنشور بعد النشر
ALTER TABLE orders ADD COLUMN IF NOT EXISTS ends_at      TIMESTAMPTZ;   -- انتهاء المنشور (24/48 ساعة بعد النشر)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reminded_at  TIMESTAMPTZ;   -- أُرسل تذكير الموعد للأدمن

CREATE INDEX IF NOT EXISTS orders_tg_post_due_idx ON orders(kind, status, ends_at) WHERE kind = 'tg_post';
