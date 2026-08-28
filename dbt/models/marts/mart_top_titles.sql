select
    tconst,
    title_type,
    primary_title,
    original_title,
    start_year,
    end_year,
    runtime_minutes,
    episode_count,
    average_rating,
    num_votes,
    genres,
    directors,
    writers,
    row_number() over (
        partition by title_type
        order by average_rating desc nulls last, num_votes desc nulls last
    ) as title_rank
from {{ ref('mart_title_search') }}
where
    title_type in ('movie', 'tvSeries')
    and num_votes >= 50000
qualify title_rank <= 500
