-- 001_init.sql — الهيكل الأساسي (الخطوة 1)
-- يُطبَّق مرة واحدة عند الإقلاع. آمن للتكرار (IF NOT EXISTS) — والمخطط المرجعي في workflow_بوت_الترويج.html §6

-- ───────────────── المستخدمون ─────────────────
CREATE TABLE IF NOT EXISTS users (
    tg_id        BIGINT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    username     TEXT,
    lang         TEXT NOT NULL DEFAULT 'ar',
    balance_usd  NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (balance_usd >= 0),
    referred_by  BIGINT,
    is_blocked   BOOLEAN NOT NULL DEFAULT FALSE,
    accepted_terms_at TIMESTAMPTZ,
    brand_kit    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ───────────────── دفتر الحركات المالية (إلحاقي فقط) ─────────────────
CREATE TABLE IF NOT EXISTS ledger (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(tg_id),
    type        TEXT NOT NULL CHECK (type IN ('topup','order_charge','refund','referral','adjustment')),
    amount_usd  NUMERIC(10,2) NOT NULL,           -- موجب = إضافة، سالب = خصم
    ref_type    TEXT,                              -- topup / order / admin
    ref_id      BIGINT,
    note        TEXT,
    admin_id    BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ledger_user_idx ON ledger(user_id, created_at DESC);

-- ───────────────── طلبات الشحن ─────────────────
CREATE TABLE IF NOT EXISTS topups (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(tg_id),
    method           TEXT NOT NULL,                -- usdt_trc20 / usdt_ton / local_xxx / stars
    amount_usd       NUMERIC(10,2) NOT NULL CHECK (amount_usd > 0),
    amount_local     NUMERIC(14,2),
    rate             NUMERIC(12,4),
    proof_file_id    TEXT,                         -- صورة الإيصال (file_id فقط — لا تنزيل)
    proof_text       TEXT,                         -- TxID أو رقم العملية
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
    admin_id         BIGINT,
    decided_at       TIMESTAMPTZ,
    reason           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS topups_status_idx ON topups(status, created_at);

-- ───────────────── الطلبات ─────────────────
CREATE TABLE IF NOT EXISTS orders (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(tg_id),
    kind             TEXT NOT NULL CHECK (kind IN ('meta_campaign','tg_ads','tg_post','copy','design','reel','montage','bundle')),
    status           TEXT NOT NULL DEFAULT 'draft',
    -- draft → awaiting_payment → paid → submitting → submitted → in_progress → active → completed
    --                                             ↘ failed_submit   ↘ paused / rejected / refunded / cancelled
    nour_id          TEXT,
    nour_status      TEXT,
    spec             JSONB NOT NULL DEFAULT '{}'::jsonb,   -- نسخة كاملة من اختيارات العميل
    price_usd        NUMERIC(10,2) NOT NULL DEFAULT 0,     -- ما دفعه العميل
    cost_usd         NUMERIC(10,2) NOT NULL DEFAULT 0,     -- ما نتوقع دفعه للشريك
    charged_usd      NUMERIC(10,2),                        -- ما خصمه الشريك فعلاً
    idempotency_key  TEXT UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at          TIMESTAMPTZ,
    submitted_at     TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_user_idx   ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders(status);

-- ───────────────── المهام اليدوية (تيليغرام / قنوات / تصميم) ─────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id                BIGSERIAL PRIMARY KEY,
    order_id          BIGINT NOT NULL REFERENCES orders(id),
    type              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','in_progress','delivered','revision','done','cancelled')),
    assignee_id       BIGINT,
    due_at            TIMESTAMPTZ,
    delivered_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    revision_count    INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status, due_at);

-- ───────────────── القنوات الشريكة ─────────────────
CREATE TABLE IF NOT EXISTS channels (
    id            BIGSERIAL PRIMARY KEY,
    owner_tg_id   BIGINT,
    title         TEXT NOT NULL,
    link          TEXT,
    niche         TEXT NOT NULL DEFAULT 'general',
    subscribers   INT NOT NULL DEFAULT 0,
    avg_views     INT NOT NULL DEFAULT 0,
    price_usd     NUMERIC(10,2) NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','removed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ───────────────── التذاكر ─────────────────
CREATE TABLE IF NOT EXISTS tickets (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(tg_id),
    order_id    BIGINT REFERENCES orders(id),
    topup_id    BIGINT REFERENCES topups(id),
    kind        TEXT NOT NULL DEFAULT 'question',   -- pause / resume / cancel / issue / topup / suggestion / question
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','closed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at   TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   BIGINT NOT NULL REFERENCES tickets(id),
    sender_id   BIGINT NOT NULL,
    is_admin    BOOLEAN NOT NULL DEFAULT FALSE,
    text        TEXT,
    file_id     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ───────────────── الإعدادات القابلة للتعديل من البوت ─────────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ───────────────── حالة المعالج (بديل MemoryStorage) ─────────────────
CREATE TABLE IF NOT EXISTS fsm_state (
    key         TEXT PRIMARY KEY,                  -- bot:chat:user
    state       TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ───────────────── سجل الأحداث ─────────────────
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT,
    order_id    BIGINT,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_created_idx ON events(created_at);

-- ───────────────── الإعدادات الافتراضية (لا تُستبدل إن وُجدت) ─────────────────
INSERT INTO settings (key, value) VALUES
  ('services', '{"meta": true, "tg_ads": true, "tg_post": true, "addons": true, "ai_reel": false}'::jsonb),
  ('min_topup_usd', '5'::jsonb),
  ('usd_rate', '0'::jsonb),
  ('payment_methods', '{}'::jsonb),
  ('sync_interval_min', '15'::jsonb),
  ('low_balance_threshold_usd', '100'::jsonb)
ON CONFLICT (key) DO NOTHING;
