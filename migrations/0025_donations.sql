-- Crypto donations through NOWPayments: an invoice per click, then its status as NOWPayments reports it (signed IPN).
-- The site never holds funds or wallets; this is a record for the admin.
CREATE TABLE donations (
  id              TEXT PRIMARY KEY,        -- our order_id
  invoice_id      TEXT,
  payment_id      TEXT,
  amount_usd      REAL NOT NULL,
  status          TEXT NOT NULL,           -- created, then NOWPayments' payment_status (waiting, confirming, confirmed, finished, partially_paid, failed, expired, refunded)
  pay_currency    TEXT,
  pay_amount      REAL,
  actually_paid   REAL,
  outcome_amount  REAL,
  outcome_currency TEXT,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER
);
CREATE INDEX donations_created ON donations(created_at);
