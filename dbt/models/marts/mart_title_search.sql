select
    core.tconst,
    core.title_type,
    core.primary_title,
    core.original_title,
    core.is_adult,
    core.start_year,
    core.end_year,
    core.runtime_minutes,
    core.episode_count,
    core.average_rating,
    core.num_votes,
    core.genres,
    core.dlt_load_id,
    credits.directors,
    credits.writers
from {{ ref('int_title_search_core') }} as core
left join {{ ref('int_title_search_credits') }} as credits using (tconst)
