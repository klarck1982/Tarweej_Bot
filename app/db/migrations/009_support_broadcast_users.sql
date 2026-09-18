-- v0.8.1 — 🎫 تذاكر الدعم + 📣 البث + 👤 بحث المستخدم وتعديل الرصيد
-- إضافات فقط، آمنة عند الإقلاع المتكرر.

-- Telegram لا يعيد رسالة المستخدم الذي حظر البوت؛ نحفظ ذلك حتى لا نكرر البث الفاشل.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked_bot BOOLEAN NOT NULL DEFAULT FALSE;

-- آخر رسالة للتذكرة: يعتمد عليها الإغلاق التلقائي بعد 72 ساعة من الصمت.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS last_msg_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS admin_msg_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE ticket_messages ADD COLUMN IF NOT EXISTS file_kind TEXT;
UPDATE tickets SET last_msg_at = COALESCE(last_msg_at, created_at) WHERE last_msg_at IS NULL;

CREATE INDEX IF NOT EXISTS tickets_open_idx ON tickets(status, last_msg_at DESC);
CREATE INDEX IF NOT EXISTS ticket_messages_ticket_idx ON ticket_messages(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS users_broadcast_idx ON users(is_blocked, is_blocked_bot, last_seen DESC);
