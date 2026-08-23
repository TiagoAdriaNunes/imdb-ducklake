{{ config(materialized='view') }}

select
    tconst,
    ordering,
    nconst,
    category,
    job,
    characters,
    dlt_load_id
from {{ ref('stg_imdb__title_principals') }}
