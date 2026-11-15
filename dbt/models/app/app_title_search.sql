-- Movies and TV series only: the title types the app lists. Partitioned by title_type (app.yml),
-- so a search reads one partition's files.
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
    array_to_string(genres, ', ') as genres_text,
    array_to_string(directors, ', ') as directors_text,
    array_to_string(writers, ', ') as writers_text
from {{ ref('mart_title_search') }}
where title_type in ('movie', 'tvSeries')
