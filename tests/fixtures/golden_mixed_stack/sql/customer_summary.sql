CREATE TABLE customer_summary AS
WITH active_users AS (
    SELECT id
    FROM customers
    WHERE status = 'active'
)
SELECT
    active_users.id,
    orders.total
FROM active_users
JOIN orders
    ON active_users.id = orders.customer_id;
