select
    tconst as key_value,
    'mart_title_search' as relation_name
from {{ ref('mart_title_search') }}
group by tconst
having count(*) > 1

union all

select
    concat_ws('|', start_year::varchar, title_type, genre) as key_value,
    'mart_genre_year_summary' as relation_name
from {{ ref('mart_genre_year_summary') }}
group by start_year, title_type, genre
having count(*) > 1

union all

select
    concat_ws('|', nconst, tconst, ordering::varchar) as key_value,
    'mart_person_filmography' as relation_name
from {{ ref('mart_person_filmography') }}
group by nconst, tconst, ordering
having count(*) > 1

union all

select
    episode_tconst as key_value,
    'mart_series_episodes' as relation_name
from {{ ref('mart_series_episodes') }}
group by episode_tconst
having count(*) > 1
