{{ config(materialized='table') }}

select
    tconst,
    'director'::varchar as crew_role,
    unnest(directors) as nconst,
    dlt_load_id
from {{ ref('stg_imdb__title_crew') }}
where directors is not null

union all

select
    tconst,
    'writer'::varchar as crew_role,
    unnest(writers) as nconst,
    dlt_load_id
from {{ ref('stg_imdb__title_crew') }}
where writers is not null
