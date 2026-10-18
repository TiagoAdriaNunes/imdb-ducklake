select
    tconst,
    primary_title,
    original_title,
    start_year,
    runtime_minutes,
    average_rating,
    num_votes,
    title_rank,
    array_to_string(genres, ', ') as genres_text,
    array_to_string(directors, ', ') as directors_text,
    array_to_string(writers, ', ') as writers_text
from {{ ref('mart_top_titles') }}
where title_type = 'movie' and title_rank <= 500
