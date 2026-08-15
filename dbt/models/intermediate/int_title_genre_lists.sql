{{ config(materialized='table') }}

select
    tconst,
    list(genre order by genre) as genres
from {{ ref('bridge_title_genres') }}
group by tconst
