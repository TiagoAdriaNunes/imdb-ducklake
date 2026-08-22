select
    core.*,
    credits.directors,
    credits.writers
from {{ ref('int_title_search_core') }} as core
left join {{ ref('int_title_search_credits') }} as credits using (tconst)
