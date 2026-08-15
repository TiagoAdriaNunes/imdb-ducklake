select
    tconst,
    average_rating,
    num_votes,
    dlt_load_id
from {{ ref('stg_imdb__title_ratings') }}
