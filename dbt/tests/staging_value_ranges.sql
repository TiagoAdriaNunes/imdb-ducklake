select
    tconst,
    'title year or runtime' as invalid_field
from {{ ref('stg_imdb__title_basics') }}
where
    start_year not between 1870 and 2200
    or end_year not between 1870 and 2200
    or runtime_minutes < 0

union all

select
    tconst,
    'rating or votes'
from {{ ref('stg_imdb__title_ratings') }}
where
    average_rating not between 0 and 10
    or num_votes < 0

union all

select
    nconst,
    'person year'
from {{ ref('stg_imdb__name_basics') }}
where
    birth_year not between 1 and 2200
    or death_year not between 1 and 2200
