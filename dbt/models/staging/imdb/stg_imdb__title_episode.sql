select
    cast("tconst" as varchar) as tconst,
    cast("parentTconst" as varchar) as parent_tconst,
    cast(nullif("seasonNumber", '\N') as integer) as season_number,
    cast(nullif("episodeNumber", '\N') as integer) as episode_number,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'title_episode') }}
