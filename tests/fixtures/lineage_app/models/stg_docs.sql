select id, body
from {{ source('raw', 'docs') }}
