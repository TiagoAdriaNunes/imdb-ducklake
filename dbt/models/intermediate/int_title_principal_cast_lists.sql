{{ config(materialized='table') }}

select
    credits.tconst,
    list(
        distinct coalesce(people.primary_name, credits.nconst)
        order by coalesce(people.primary_name, credits.nconst)
    ) as principal_cast
from {{ ref('bridge_title_credits') }} as credits
left join {{ ref('dim_people') }} as people using (nconst)
where credits.category in ('actor', 'actress', 'self')
group by credits.tconst
