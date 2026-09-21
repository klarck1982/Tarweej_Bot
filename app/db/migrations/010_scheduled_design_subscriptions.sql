-- v0.9.0 — باقات تصميم مجدولة: تصميم + نص يومي لكل عميل
-- المحتوى يبقى في Telegram عبر file_id؛ قاعدة البيانات تحفظ الترتيب والموعد والحالة فقط.

CREATE TABLE IF NOT EXISTS scheduled_subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(tg_id),
    order_id        BIGINT REFERENCES orders(id),
    package_code    TEXT NOT NULL,
    package_title   TEXT NOT NULL,
    price_usd       NUMERIC(10,2) NOT NULL CHECK (price_usd >= 0),
    total_items     INT NOT NULL CHECK (total_items BETWEEN 1 AND 365),
    duration_days   INT NOT NULL CHECK (duration_days BETWEEN 1 AND 365),
    send_time       TIME NOT NULL DEFAULT '20:00',
    timezone        TEXT NOT NULL DEFAULT 'Asia/Damascus',
    start_at        TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'awaiting_assets'
                    CHECK (status IN ('awaiting_assets','scheduled','paused','completed','cancelled','refunded')),
    sent_count      INT NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
    next_send_at    TIMESTAMPTZ,
    last_sent_at    TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS scheduled_subscriptions_user_idx ON scheduled_subscriptions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS scheduled_subscriptions_due_idx ON scheduled_subscriptions(status, next_send_at)
    WHERE next_send_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS scheduled_subscriptions_order_idx ON scheduled_subscriptions(order_id);

CREATE TABLE IF NOT EXISTS scheduled_subscription_items (
    id              BIGSERIAL PRIMARY KEY,
    subscription_id BIGINT NOT NULL REFERENCES scheduled_subscriptions(id) ON DELETE CASCADE,
    seq             INT NOT NULL CHECK (seq >= 1),
    file_kind       TEXT NOT NULL CHECK (file_kind IN ('photo','document','video')),
    file_id         TEXT NOT NULL,
    copy_text       TEXT NOT NULL DEFAULT '',
    scheduled_at    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','sending','sent','failed','skipped')),
    attempts        INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    sent_at         TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(subscription_id, seq)
);
CREATE INDEX IF NOT EXISTS scheduled_items_due_idx ON scheduled_subscription_items(status, scheduled_at)
    WHERE scheduled_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS scheduled_items_subscription_idx ON scheduled_subscription_items(subscription_id, seq);

-- لا نحذف طلبات الخدمة القديمة؛ نربط الطلب المجدول بالاشتراك عند إنشائه.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_subscription_id BIGINT REFERENCES scheduled_subscriptions(id);
CREATE INDEX IF NOT EXISTS orders_scheduled_subscription_idx ON orders(scheduled_subscription_id)
    WHERE scheduled_subscription_id IS NOT NULL;
