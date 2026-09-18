select id, email
from {{ source('raw', 'users') }}
where email is not null
