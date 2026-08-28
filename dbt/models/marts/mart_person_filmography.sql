with person_credits as (
    select
        tconst,
        nconst,
        ordering,
        category,
        job,
        characters,
        dlt_load_id
    from {{ ref('bridge_title_credits') }}

    union all

    select
        tconst,
        nconst,
        null::integer as ordering,
        crew_role as category,
        null::varchar as job,
        null::varchar[] as characters,
        dlt_load_id
    from {{ ref('bridge_title_crew') }}
)

select
    people.nconst,
    people.primary_name,
    credits.tconst,
    titles.primary_title,
    titles.title_type,
    titles.start_year,
    credits.ordering,
    credits.category,
    credits.job,
    credits.characters,
    ratings.average_rating,
    ratings.num_votes,
    credits.dlt_load_id
from person_credits as credits
inner join {{ ref('dim_people') }} as people using (nconst)
inner join {{ ref('dim_titles') }} as titles using (tconst)
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
-- Physically clusters rows by tconst so a remote `WHERE tconst = $tconst` lookup (title_cast.sql)
-- can prune most Parquet row groups via min/max stats instead of scanning the whole table -
-- unsorted, a single title's credits could land in any of this table's ~125M rows (see ADR 0013).
order by credits.tconst, credits.ordering, credits.nconst
