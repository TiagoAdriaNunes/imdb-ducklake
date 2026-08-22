select
    titles.tconst as "IMDb ID",
    titles.primary_title as "Primary Title",
    titles.original_title as "Original Title",
    titles.start_year as "Start Year",
    titles.end_year as "End Year",
    titles.runtime_minutes as "Runtime (min)",
    titles.episode_count as "Episodes",
    titles.average_rating as "Rating",
    titles.num_votes as "Votes",
    array_to_string(titles.genres, ', ') as "Genres",
    array_to_string(titles.directors, ', ') as "Directors",
    array_to_string(titles.writers, ', ') as "Writers"
from marts.mart_title_search as titles
where
    titles.title_type = 'tvSeries'
    and titles.primary_title ilike '%' || '{{ query | replace("'", "''") }}' || '%'
order by titles.average_rating desc nulls last, titles.num_votes desc nulls last
limit {{ limit }}
