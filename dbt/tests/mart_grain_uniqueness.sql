select
    tconst as key_value,
    'mart_title_search' as relation_name
from {{ ref('mart_title_search') }}
group by tconst
having count(*) > 1

union all

select
    concat_ws('|', start_year::varchar, title_type, genre),
    'mart_genre_year_summary'
from {{ ref('mart_genre_year_summary') }}
group by start_year, title_type, genre
having count(*) > 1

union all

select
    concat_ws('|', nconst, tconst, ordering::varchar),
    'mart_person_filmography'
from {{ ref('mart_person_filmography') }}
group by nconst, tconst, ordering
having count(*) > 1

union all

select
    episode_tconst,
    'mart_series_episodes'
from {{ ref('mart_series_episodes') }}
group by episode_tconst
having count(*) > 1
