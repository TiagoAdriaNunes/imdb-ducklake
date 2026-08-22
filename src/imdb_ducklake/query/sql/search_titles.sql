select
    tconst as "IMDb ID",
    title_type as "Type",
    primary_title as "Primary Title",
    original_title as "Original Title",
    start_year as "Start Year",
    end_year as "End Year",
    runtime_minutes as "Runtime (min)",
    average_rating as "Rating",
    num_votes as "Votes",
    array_to_string(genres, ', ') as "Genres",
    array_to_string(directors, ', ') as "Directors",
    array_to_string(principal_cast, ', ') as "Cast"
from marts.mart_title_search
where
    primary_title ilike '%' || '{{ query | replace("'", "''") }}' || '%'
    and title_type in ('movie', 'tvSeries')
    {% if title_types %}
        and title_type in (
            {% for title_type in title_types %}
                '{{ title_type | replace("'", "''") }}'{% if not loop.last %},{% endif %}
            {% endfor %}
        )
    {% endif %}
order by average_rating desc nulls last, num_votes desc nulls last
limit {{ limit }}
