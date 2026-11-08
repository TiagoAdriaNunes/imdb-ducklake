-- The bridge and dimension grain tests cover duplicates within each input.
-- A duplicate filmography key can only come from a principal with a null
-- ordering that also appears as a director or writer crew credit.
select
    principals.nconst,
    principals.tconst,
    principals.category
from {{ ref('bridge_title_credits') }} as principals
inner join {{ ref('bridge_title_crew') }} as crew
    on
        principals.tconst = crew.tconst
        and principals.nconst = crew.nconst
        and principals.category = crew.crew_role
inner join {{ ref('dim_people') }} as people on principals.nconst = people.nconst
inner join {{ ref('dim_titles') }} as titles on principals.tconst = titles.tconst
where principals.ordering is null
