select
    cast("titleId" as varchar) as title_id,
    cast(nullif("ordering", '\N') as integer) as ordering,
    cast(nullif("title", '\N') as varchar) as title,
    cast(nullif("region", '\N') as varchar) as region,
    cast(nullif("language", '\N') as varchar) as language,
    cast(nullif("isOriginalTitle", '\N') as boolean) as is_original_title,
    cast("_dlt_load_id" as varchar) as dlt_load_id,
    cast(string_split(nullif("types", '\N'), ',') as varchar[]) as types,
    cast(string_split(nullif("attributes", '\N'), ',') as varchar[]) as attributes
from {{ source('imdb_raw', 'title_akas') }}
