select
    tconst as "IMDb ID",
    primary_title as "Primary Title",
    original_title as "Original Title",
    start_year as "Start Year",
    runtime_minutes as "Runtime (min)",
    average_rating as "Rating",
    num_votes as "Votes",
    array_to_string(genres, ', ') as "Genres",
    array_to_string(directors, ', ') as "Directors",
    array_to_string(principal_cast, ', ') as "Cast"
from marts.mart_title_search
where
    title_type = 'movie'
    and primary_title ilike '%' || '{{ query | replace("'", "''") }}' || '%'
order by average_rating desc nulls last, num_votes desc nulls last
limit {{ limit }}
