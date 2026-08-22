{{ config(materialized='table') }}

select
    crew.tconst,
    coalesce(
        list(people.primary_name order by people.primary_name)
        filter (where people.primary_name is not null),
        []
    ) as writers
from {{ ref('bridge_title_crew') }} as crew
left join {{ ref('dim_people') }} as people using (nconst)
where crew.crew_role = 'writer'
group by crew.tconst
