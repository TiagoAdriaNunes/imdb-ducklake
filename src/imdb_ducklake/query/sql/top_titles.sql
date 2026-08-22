select
    tconst,
    title_type,
    primary_title,
    original_title,
    is_adult,
    start_year,
    end_year,
    runtime_minutes,
    average_rating,
    num_votes,
    genres,
    directors,
    principal_cast,
    dlt_load_id
from marts.mart_title_search
order by num_votes desc nulls last
limit ?
