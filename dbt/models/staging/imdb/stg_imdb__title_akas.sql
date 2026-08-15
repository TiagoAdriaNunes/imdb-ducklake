select
    cast("titleId" as varchar) as title_id,
    cast(nullif("ordering", '\N') as integer) as ordering,
    cast(nullif("title", '\N') as varchar) as title,
    cast(nullif("region", '\N') as varchar) as region,
    cast(nullif("language", '\N') as varchar) as language,
    string_split(nullif("types", '\N'), ',')::varchar[] as types,
    string_split(nullif("attributes", '\N'), ',')::varchar[] as attributes,
    cast(nullif("isOriginalTitle", '\N') as boolean) as is_original_title,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'title_akas') }}
