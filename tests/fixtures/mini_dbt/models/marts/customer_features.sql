select
    users.id,
    orders.total
from {{ ref('stg_users') }} as users
join {{ ref('stg_orders') }} as orders
    on users.id = orders.user_id
