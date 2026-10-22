select
    tconst,
    primary_title,
    original_title,
    start_year,
    end_year,
    runtime_minutes,
    episode_count,
    average_rating,
    num_votes,
    title_rank,
    array_to_string(genres, ', ') as genres_text,
    array_to_string(directors, ', ') as directors_text,
    array_to_string(writers, ', ') as writers_text
from {{ ref('mart_top_titles') }}
where title_type = 'tvSeries' and title_rank <= 500
