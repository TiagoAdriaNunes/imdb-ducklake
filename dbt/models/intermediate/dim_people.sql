select
    nconst,
    primary_name,
    birth_year,
    death_year,
    primary_professions,
    known_for_titles,
    dlt_load_id
from {{ ref('stg_imdb__name_basics') }}
where primary_name is not null
