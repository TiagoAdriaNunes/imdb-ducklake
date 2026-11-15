select
    tconst as "IMDb ID",
    primary_title as "Primary Title",
    original_title as "Original Title",
    start_year as "Start Year",
    runtime_minutes as "Runtime (min)",
    average_rating as "Rating",
    num_votes as "Votes",
    genres_text as "Genres",
    directors_text as "Directors",
    writers_text as "Writers"
from app.app_title_search
where
    title_type = 'movie'
    and primary_title ilike '%' || '{{ query | replace("'", "''") }}' || '%'
order by average_rating desc nulls last, num_votes desc nulls last
limit {{ limit }}
