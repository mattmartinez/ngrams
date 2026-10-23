-- Aurora MySQL 3.07 (MySQL 8.0.36 compatible) in prod; plan.json was captured on the staging copy (MySQL 8.0.46,
-- a 1:64 sample: orders table_rows ≈ 2,000,000). Prod orders: table_rows ≈ 128,400,000; data+index ≈ 61 GB.
-- ~2,400 inserts/min and ~900 status updates/min on prod orders during business hours
-- This query is the "open orders" tab in the merchant app: ~1,100 calls/min, avg 180 ms, p95 1.4 s. The slow calls are
-- merchants with few or zero open orders, not the big ones.

CREATE TABLE `orders` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `customer_id` bigint NOT NULL,
  `status` varchar(16) NOT NULL,
  `total` decimal(12,2) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_orders_customer` (`customer_id`),
  KEY `idx_orders_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SHOW INDEX FROM orders on staging (Cardinality): PRIMARY 1991914, idx_orders_customer 51855, idx_orders_created 1991914
-- SELECT status, COUNT(*) FROM orders GROUP BY status:  open 9.8% | shipped 61% | cancelled 12% | refunded 17%
-- customer 88213 has 50,000 orders on staging (2.5% of the table), 6,000 of them open; prod ratio is the same

-- The query:
-- SELECT id, status, total, created_at
-- FROM orders
-- WHERE customer_id = 88213 AND status = 'open'
-- ORDER BY created_at DESC
-- LIMIT 20;
