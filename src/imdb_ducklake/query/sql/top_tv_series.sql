select
    tconst as "IMDb ID",
    primary_title as "Primary Title",
    original_title as "Original Title",
    start_year as "Start Year",
    end_year as "End Year",
    runtime_minutes as "Runtime (min)",
    episode_count as "Episodes",
    average_rating as "Rating",
    num_votes as "Votes",
    genres_text as "Genres",
    directors_text as "Directors",
    writers_text as "Writers"
from marts.mart_top_tv_series
order by title_rank
