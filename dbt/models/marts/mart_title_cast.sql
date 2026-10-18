select
    tconst,
    nconst,
    primary_name,
    category,
    ordering,
    characters
from {{ ref('mart_person_filmography') }}
where category in ('actor', 'actress', 'self')
-- Sorted so remote `where tconst = ...` lookups (title_cast.sql) prune Parquet row groups.
-- No title column: the app has it already, and it would widen this sort.
order by tconst
