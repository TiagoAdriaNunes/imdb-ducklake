select
    titles.tconst,
    titles.title_type,
    titles.primary_title,
    titles.original_title,
    titles.is_adult,
    titles.start_year,
    titles.end_year,
    titles.runtime_minutes,
    ratings.average_rating,
    ratings.num_votes,
    genres.genres,
    directors.directors,
    principals.principal_cast,
    titles.dlt_load_id
from {{ ref('dim_titles') }} as titles
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
left join {{ ref('int_title_genre_lists') }} as genres using (tconst)
left join {{ ref('int_title_director_lists') }} as directors using (tconst)
left join {{ ref('int_title_principal_cast_lists') }} as principals using (tconst)
