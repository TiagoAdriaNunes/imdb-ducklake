{{ config(materialized='table') }}

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
    titles.dlt_load_id
from {{ ref('dim_titles') }} as titles
left join episode_counts using (tconst)
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
left join {{ ref('int_title_genre_lists') }} as genres using (tconst)
