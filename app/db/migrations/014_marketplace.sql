-- 014_marketplace.sql — v1.0.0: 💼 سوق القنوات ذاتي الخدمة
-- صاحب القناة يسجّل قناته بنفسه (البوت مشرف فيها)، يقبل/يرفض الطلبات، البوت ينشر ويتحقق ويحذف،
-- والأرباح تُحجز حتى انتهاء المنشور + فترة البلاغات ثم تصبح قابلة للسحب.
-- إضافات فقط — آمنة للتكرار، ولا تغيّر سلوك القنوات التي يديرها الأدمن (owner_user_id = NULL).

-- ───────────── القناة ─────────────
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS owner_user_id  BIGINT REFERENCES users(tg_id);
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS chat_id        BIGINT;
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS mp_status      TEXT;      -- NULL = يديرها الأدمن | draft | pending | approved | rejected | suspended
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS verified_at    TIMESTAMPTZ;
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS subs_checked_at TIMESTAMPTZ;
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS mp_note        TEXT NOT NULL DEFAULT '';   -- سبب الرفض/الإيقاف
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS rating_sum     INT NOT NULL DEFAULT 0;
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS rating_n       INT NOT NULL DEFAULT 0;
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS done_n         INT NOT NULL DEFAULT 0;     -- منشورات اكتملت
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS reject_n       INT NOT NULL DEFAULT 0;     -- رفض/اعتذار
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS timeout_n      INT NOT NULL DEFAULT 0;     -- لم يرد في المهلة
ALTER TABLE partner_channels ADD COLUMN IF NOT EXISTS early_n        INT NOT NULL DEFAULT 0;     -- حذف المنشور قبل انتهاء المدة
CREATE UNIQUE INDEX IF NOT EXISTS partner_channels_chat_uidx ON partner_channels(chat_id)
    WHERE chat_id IS NOT NULL AND NOT archived AND mp_status IN ('draft','pending','approved','suspended');
CREATE INDEX IF NOT EXISTS partner_channels_owner_idx ON partner_channels(owner_user_id) WHERE owner_user_id IS NOT NULL;

-- ───────────── الطلب ─────────────
ALTER TABLE orders ADD COLUMN IF NOT EXISTS owner_user_id    BIGINT;          -- صاحب القناة (طلبات السوق فقط)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS owner_deadline   TIMESTAMPTZ;     -- آخر موعد لقبول/رفض صاحب القناة
ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel_chat_id  BIGINT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel_msg_ids  JSONB;           -- رسائل المنشور في القناة (للتحقق والحذف)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS publishing_until TIMESTAMPTZ;     -- حجز النشر (يمنع النشر المزدوج)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS publish_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS verified_at      TIMESTAMPTZ;     -- آخر تحقق من وجود المنشور
ALTER TABLE orders ADD COLUMN IF NOT EXISTS unpublished_at   TIMESTAMPTZ;     -- حُذف المنشور من القناة بعد الانتهاء
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payout_status    TEXT;            -- held | disputed | available | reversed
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payout_at        TIMESTAMPTZ;     -- متى تتحرر الأرباح
ALTER TABLE orders ADD COLUMN IF NOT EXISTS rating           SMALLINT;
CREATE INDEX IF NOT EXISTS orders_owner_idx ON orders(owner_user_id, id DESC) WHERE owner_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS orders_payout_idx ON orders(payout_status, payout_at) WHERE payout_status IS NOT NULL;

-- ───────────── أرباح أصحاب القنوات: رصيدان منفصلان عن رصيد الشراء ─────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS earn_held_usd  NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS earn_avail_usd NUMERIC(10,2) NOT NULL DEFAULT 0;
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_earn_nonneg CHECK (earn_held_usd >= 0 AND earn_avail_usd >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- دفتر الأرباح (إلحاقي فقط): مجموع كل bucket يساوي عمود المستخدم المقابل دائماً
CREATE TABLE IF NOT EXISTS earnings_ledger (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(tg_id),
    bucket      TEXT NOT NULL CHECK (bucket IN ('held','avail')),
    type        TEXT NOT NULL CHECK (type IN ('earning','release','reverse','payout','payout_return','convert','adjust')),
    amount_usd  NUMERIC(10,2) NOT NULL,          -- موجب = إضافة، سالب = خصم
    ref_type    TEXT,                             -- order / payout
    ref_id      BIGINT,
    note        TEXT,
    admin_id    BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS earnings_ledger_user_idx ON earnings_ledger(user_id, created_at DESC);

-- طلبات السحب
CREATE TABLE IF NOT EXISTS payouts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(tg_id),
    amount_usd  NUMERIC(10,2) NOT NULL CHECK (amount_usd > 0),
    method      TEXT NOT NULL,                    -- usdt_trc20 | usdt_bep20 | shamcash
    address     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','rejected')),
    admin_id    BIGINT,
    note        TEXT,
    admin_msg_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS payouts_one_pending ON payouts(user_id) WHERE status = 'pending';

-- تحويل الأرباح إلى رصيد إعلانات = حركة جديدة في دفتر الرصيد
ALTER TABLE ledger DROP CONSTRAINT IF EXISTS ledger_type_check;
ALTER TABLE ledger ADD CONSTRAINT ledger_type_check
    CHECK (type IN ('topup','order_charge','refund','referral','adjustment','earnings'));
