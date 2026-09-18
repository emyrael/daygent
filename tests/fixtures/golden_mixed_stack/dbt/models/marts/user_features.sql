select
    users.id,
    users.email
from {{ ref('stg_users') }} as users
