select
    "tconst",
    "isAdult"
from {{ source('imdb_raw', 'title_basics') }}
where "isAdult" not in ('0', '1', '\N')
