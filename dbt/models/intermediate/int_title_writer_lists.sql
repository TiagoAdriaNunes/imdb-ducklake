{{ config(materialized='table') }}

select
    crew.tconst,
    list(
        coalesce(people.primary_name, crew.nconst)
        order by coalesce(people.primary_name, crew.nconst), crew.nconst
    ) as writers,
    list(crew.nconst order by coalesce(people.primary_name, crew.nconst), crew.nconst) as writer_ids
from {{ ref('bridge_title_crew') }} as crew
left join {{ ref('dim_people') }} as people using (nconst)
where crew.crew_role = 'writer'
group by crew.tconst
