select
    cast("tconst" as varchar) as tconst,
    cast(nullif("titleType", '\N') as varchar) as title_type,
    cast(nullif("primaryTitle", '\N') as varchar) as primary_title,
    cast(nullif("originalTitle", '\N') as varchar) as original_title,
    cast(nullif("isAdult", '\N') as boolean) as is_adult,
    cast(nullif("startYear", '\N') as smallint) as start_year,
    cast(nullif("endYear", '\N') as smallint) as end_year,
    cast(nullif("runtimeMinutes", '\N') as integer) as runtime_minutes,
    cast("_dlt_load_id" as varchar) as dlt_load_id,
    cast(string_split(nullif("genres", '\N'), ',') as varchar[]) as genres
from {{ source('imdb_raw', 'title_basics') }}
