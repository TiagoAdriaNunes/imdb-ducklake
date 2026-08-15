with genre_lists as (
    select
        tconst,
        list_sort(list_distinct(list(genre))) as genres
    from {{ ref('bridge_title_genres') }}
    group by tconst
),

director_lists as (
    select
        crew.tconst,
        list_sort(list_distinct(list(coalesce(people.primary_name, crew.nconst)))) as directors
    from {{ ref('bridge_title_crew') }} as crew
    left join {{ ref('dim_people') }} as people using (nconst)
    where crew.crew_role = 'director'
    group by crew.tconst
),

principal_lists as (
    select
        credits.tconst,
        list_sort(list_distinct(list(coalesce(people.primary_name, credits.nconst)))) as principal_cast
    from {{ ref('bridge_title_credits') }} as credits
    left join {{ ref('dim_people') }} as people using (nconst)
    where credits.category in ('actor', 'actress', 'self')
    group by credits.tconst
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
    ratings.average_rating,
    ratings.num_votes,
    genres.genres,
    directors.directors,
    principals.principal_cast,
    titles.dlt_load_id
from {{ ref('dim_titles') }} as titles
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
left join genre_lists as genres using (tconst)
left join director_lists as directors using (tconst)
left join principal_lists as principals using (tconst)
