select
    tconst,
    unnest(genres) as genre,
    dlt_load_id
from {{ ref('stg_imdb__title_basics') }}
where genres is not null
