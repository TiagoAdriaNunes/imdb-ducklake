select
    tconst,
    ordering::varchar as grain_value,
    'title_akas' as relation_name
from {{ ref('bridge_title_akas') }}
group by tconst, ordering
having count(*) > 1

union all

select
    tconst,
    ordering::varchar as grain_value,
    'title_credits' as relation_name
from {{ ref('bridge_title_credits') }}
group by tconst, ordering
having count(*) > 1

union all

select
    tconst,
    genre as grain_value,
    'title_genres' as relation_name
from {{ ref('bridge_title_genres') }}
group by tconst, genre
having count(*) > 1

union all

select
    tconst,
    crew_role || ':' || nconst as grain_value,
    'title_crew' as relation_name
from {{ ref('bridge_title_crew') }}
group by tconst, crew_role, nconst
having count(*) > 1
