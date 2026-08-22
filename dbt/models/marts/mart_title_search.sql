with episode_counts as (
    select
        parent_tconst as tconst,
        count(*) as episode_count
    from {{ ref('fct_episodes') }}
    group by parent_tconst
)

select
    titles.tconst,
    titles.title_type,
    titles.primary_title,
    titles.original_title,
    titles.is_adult,
    titles.start_year,
    titles.end_year,
    titles.runtime_minutes,
    episode_counts.episode_count,
    ratings.average_rating,
    ratings.num_votes,
    genres.genres,
    directors.directors,
    directors.director_ids,
    writers.writers,
    writers.writer_ids,
    principals.principal_cast,
    principals.principal_cast_ids,
    titles.dlt_load_id
from {{ ref('dim_titles') }} as titles
left join episode_counts using (tconst)
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
left join {{ ref('int_title_genre_lists') }} as genres using (tconst)
left join {{ ref('int_title_director_lists') }} as directors using (tconst)
left join {{ ref('int_title_writer_lists') }} as writers using (tconst)
left join {{ ref('int_title_principal_cast_lists') }} as principals using (tconst)
