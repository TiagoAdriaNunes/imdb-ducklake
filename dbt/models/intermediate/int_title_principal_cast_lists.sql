{{ config(materialized='table') }}

select
    credits.tconst,
    list(
        coalesce(people.primary_name, credits.nconst)
        order by credits.ordering
    ) as principal_cast,
    list(credits.nconst order by credits.ordering) as principal_cast_ids
from {{ ref('bridge_title_credits') }} as credits
left join {{ ref('dim_people') }} as people using (nconst)
where credits.category in ('actor', 'actress', 'self')
group by credits.tconst
