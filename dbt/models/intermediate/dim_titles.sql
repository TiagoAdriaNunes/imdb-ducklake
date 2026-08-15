select
    tconst,
    title_type,
    primary_title,
    original_title,
    is_adult,
    start_year,
    end_year,
    runtime_minutes,
    genres,
    dlt_load_id
from {{ ref('stg_imdb__title_basics') }}
