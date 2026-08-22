{{ config(materialized='table') }}

select
    titles.tconst,
    directors.directors,
    writers.writers
from {{ ref('dim_titles') }} as titles
left join {{ ref('int_title_director_lists') }} as directors using (tconst)
left join {{ ref('int_title_writer_lists') }} as writers using (tconst)
