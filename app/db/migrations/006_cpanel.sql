-- v0.5.0 — Cpanel: إعدادات عامة قابلة للتعديل من الـ Mini App.
-- حد تنبيه رصيد Nour: القيمة المتفق عليها 50$ (البذرة القديمة كانت 100).
UPDATE settings SET value = '50'::jsonb, updated_at = now()
 WHERE key = 'low_balance_threshold_usd'
   AND (value #>> '{}') ~ '^[0-9.]+$' AND (value #>> '{}')::numeric = 100;

INSERT INTO settings (key, value) VALUES
  ('max_topup_usd', '1000'::jsonb),
  ('topup_presets_usd', '[5, 10, 20, 50, 100]'::jsonb),
  ('order_draft_days', '7'::jsonb),
  ('maintenance', 'false'::jsonb),
  ('disabled_style', '"lock"'::jsonb)
ON CONFLICT (key) DO NOTHING;

CREATE INDEX IF NOT EXISTS events_type_idx ON events(type, id DESC);
