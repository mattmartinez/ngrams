-- PostgreSQL 16 (Aurora PostgreSQL 16.4 in prod). plan.json was captured on the staging copy: PostgreSQL 16.15,
-- a 1:9 sample of prod (users 101,390 rows, orders 2,032,000 rows) restored with prod's statistics as of 2026-09-28.
-- Prod: users ~900,000, orders ~41,000,000. Same plan shape on both.
-- Query runs ~40 times/minute from the account dashboard; prod p95 is 3.1 s, target is < 300 ms. On staging it runs in 25 ms.

CREATE TABLE users (
    id            bigint PRIMARY KEY,
    email         text NOT NULL,
    region        text NOT NULL,
    signup_source text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_region ON users (region);

CREATE TABLE orders (
    id         bigint PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES users (id),
    status     text NOT NULL,
    total      numeric(12,2) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_user_id ON orders (user_id);
CREATE INDEX idx_orders_created_at ON orders (created_at);

-- pg_stat_user_tables for users (staging): n_live_tup 101390, n_mod_since_analyze 1390, last_analyze 2026-09-28 03:12 (UTC)
-- (the referral programme launched 2026-10-01; 1,390 staging users / ~12.5k prod users have signed up through it since, 41% in us-east)
-- SELECT attname, n_distinct, most_common_vals, most_common_freqs FROM pg_stats WHERE tablename='users' AND attname IN ('region','signup_source');
--  region        | 6 | {us-east,us-west,eu-central,ap-south,af-south,sa-east}    | {0.40,0.31,0.14,0.08,0.03,0.03}
--  signup_source | 9 | {organic,paid,partner,event,affiliate,press,social}       | {0.6228,0.2046,0.0913,0.0306,0.0301,0.0156,0.0048}
--  ('referral' is not in the MCV list at all: the statistics predate the launch)
-- SELECT count(*) FROM users WHERE region='us-east' AND signup_source='referral';  -- 587 on staging today (~5,120 on prod)

-- The query:
-- SELECT o.id, o.total
-- FROM orders o JOIN users u ON u.id = o.user_id
-- WHERE u.region = 'us-east' AND u.signup_source = 'referral'
-- ORDER BY o.created_at DESC
-- LIMIT 100;
