{{ config(materialized='table') }}

select
    crew.tconst,
    list(
        distinct coalesce(people.primary_name, crew.nconst)
        order by coalesce(people.primary_name, crew.nconst)
    ) as directors
from {{ ref('bridge_title_crew') }} as crew
left join {{ ref('dim_people') }} as people using (nconst)
where crew.crew_role = 'director'
group by crew.tconst
