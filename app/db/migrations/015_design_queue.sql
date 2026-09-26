-- 015_design_queue.sql — «باقة تصميم يومي»: نموذج طابور الأزواج + وجه التسليم (محادثة/قناة)
--
-- • كل زوج (تصميم+نص) يدخل طابور الاشتراك (seq تلقائي FIFO) ويُسلَّم واحداً يومياً في ساعة الاشتراك.
-- • وجه التسليم: محادثة الزبون (target_chat_id=user_id) أو قناة خاصة له (البوت مشرفاً فيها).
-- • الاشتراكات اليدوية (زبائن يدفعون برّا البوت): بلا user_id/order_id.
-- • missed_slot: فات موعد والطابور كان فارغاً ← أول زوج يصل يُرسل فوراً مع اعتذار.
-- • فهرس فريد: وجه واحد فقط لاشتراك نشط (يمنع ربط قناة زبونين معاً بالخطأ).

ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_chat_id BIGINT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_kind   TEXT NOT NULL DEFAULT 'user';
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_title  TEXT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS customer_name TEXT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS is_manual     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS missed_slot   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS buffer_alert_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS note TEXT;

-- اشتراكات يدوية بلا حساب بوت
ALTER TABLE scheduled_subscriptions ALTER COLUMN user_id DROP NOT NULL;

-- الأقدم: وجه التسليم = محادثة المستخدم نفسه
UPDATE scheduled_subscriptions SET target_chat_id = user_id, customer_name = user_name
 WHERE target_chat_id IS NULL;

-- وجه واحد فقط لكل اشتراك نشط — أي محاولة ربط قناة زبونين تفشل بالقاعدة نفسها
CREATE UNIQUE INDEX IF NOT EXISTS scheduled_subscriptions_active_target_uq
    ON scheduled_subscriptions(target_chat_id)
    WHERE status IN ('awaiting_assets','scheduled','paused') AND target_chat_id IS NOT NULL;

-- طابور الأزواج: seq = ترتيب الإرسال (FIFO) يُولَّد تلقائياً عند الإدخال
CREATE INDEX IF NOT EXISTS scheduled_items_queue_idx
    ON scheduled_subscription_items(subscription_id, status, seq);
