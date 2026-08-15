select
    title_id as tconst,
    ordering,
    title,
    region,
    language,
    types,
    attributes,
    is_original_title,
    dlt_load_id
from {{ ref('stg_imdb__title_akas') }}
