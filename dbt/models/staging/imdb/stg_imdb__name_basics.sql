select
    cast("nconst" as varchar) as nconst,
    cast(nullif("primaryName", '\N') as varchar) as primary_name,
    cast(nullif("birthYear", '\N') as smallint) as birth_year,
    cast(nullif("deathYear", '\N') as smallint) as death_year,
    string_split(nullif("primaryProfession", '\N'), ',')::varchar[] as primary_professions,
    string_split(nullif("knownForTitles", '\N'), ',')::varchar[] as known_for_titles,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'name_basics') }}
