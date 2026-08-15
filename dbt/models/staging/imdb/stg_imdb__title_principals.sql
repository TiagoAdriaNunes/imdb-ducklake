select
    cast("tconst" as varchar) as tconst,
    cast(nullif("ordering", '\N') as integer) as ordering,
    cast("nconst" as varchar) as nconst,
    cast(nullif("category", '\N') as varchar) as category,
    cast(nullif("job", '\N') as varchar) as job,
    from_json(nullif("characters", '\N'), '["VARCHAR"]') as characters,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'title_principals') }}
