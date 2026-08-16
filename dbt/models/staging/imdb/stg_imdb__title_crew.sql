select
    cast("tconst" as varchar) as tconst,
    cast("_dlt_load_id" as varchar) as dlt_load_id,
    cast(string_split(nullif("directors", '\N'), ',') as varchar[]) as directors,
    cast(string_split(nullif("writers", '\N'), ',') as varchar[]) as writers
from {{ source('imdb_raw', 'title_crew') }}
