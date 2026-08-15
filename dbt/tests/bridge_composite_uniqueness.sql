select tconst, ordering::varchar as grain_value, 'title_akas' as relation_name
from {{ ref('bridge_title_akas') }}
group by tconst, ordering
having count(*) > 1

union all

select tconst, ordering::varchar, 'title_credits'
from {{ ref('bridge_title_credits') }}
group by tconst, ordering
having count(*) > 1

union all

select tconst, genre, 'title_genres'
from {{ ref('bridge_title_genres') }}
group by tconst, genre
having count(*) > 1

union all

select tconst, crew_role || ':' || nconst, 'title_crew'
from {{ ref('bridge_title_crew') }}
group by tconst, crew_role, nconst
having count(*) > 1
