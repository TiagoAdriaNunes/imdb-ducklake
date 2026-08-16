select
    cast("tconst" as varchar) as tconst,
    cast(nullif("ordering", '\N') as integer) as ordering,
    cast("nconst" as varchar) as nconst,
    cast(nullif("category", '\N') as varchar) as category,
    cast(nullif("job", '\N') as varchar) as job,
    cast("_dlt_load_id" as varchar) as dlt_load_id,
    from_json(nullif("characters", '\N'), '["VARCHAR"]') as characters
from {{ source('imdb_raw', 'title_principals') }}
