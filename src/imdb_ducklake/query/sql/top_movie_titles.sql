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
    array_to_string(writers, ', ') as "Writers"
from marts.mart_top_titles
where title_type = 'movie'
order by title_rank
limit {{ limit }}
