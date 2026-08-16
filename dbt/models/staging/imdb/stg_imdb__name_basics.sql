select
    cast("nconst" as varchar) as nconst,
    cast(nullif("primaryName", '\N') as varchar) as primary_name,
    cast(nullif("birthYear", '\N') as smallint) as birth_year,
    cast(nullif("deathYear", '\N') as smallint) as death_year,
    cast("_dlt_load_id" as varchar) as dlt_load_id,
    cast(string_split(nullif("primaryProfession", '\N'), ',') as varchar[]) as primary_professions,
    cast(string_split(nullif("knownForTitles", '\N'), ',') as varchar[]) as known_for_titles
from {{ source('imdb_raw', 'name_basics') }}
