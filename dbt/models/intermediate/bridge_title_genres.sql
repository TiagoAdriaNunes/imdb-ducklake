{{ config(materialized='table') }}

select
    tconst,
    dlt_load_id,
    unnest(genres) as genre
from {{ ref('stg_imdb__title_basics') }}
where genres is not null
