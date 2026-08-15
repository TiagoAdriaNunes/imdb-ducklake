select
    cast("tconst" as varchar) as tconst,
    string_split(nullif("directors", '\N'), ',')::varchar[] as directors,
    string_split(nullif("writers", '\N'), ',')::varchar[] as writers,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'title_crew') }}
