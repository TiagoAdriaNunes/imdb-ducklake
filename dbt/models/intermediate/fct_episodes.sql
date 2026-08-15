select
    tconst,
    parent_tconst,
    season_number,
    episode_number,
    dlt_load_id
from {{ ref('stg_imdb__title_episode') }}
