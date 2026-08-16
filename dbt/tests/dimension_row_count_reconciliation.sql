select 'dim_titles' as relation_name
where
    (select count(*) from {{ ref('dim_titles') }})
    != (select count(*) from {{ ref('stg_imdb__title_basics') }})

union all

select 'dim_people'
where
    (select count(*) from {{ ref('dim_people') }})
    != (select count(*) from {{ ref('stg_imdb__name_basics') }})

union all

select 'fct_title_ratings'
where
    (select count(*) from {{ ref('fct_title_ratings') }})
    != (select count(*) from {{ ref('stg_imdb__title_ratings') }})

union all

select 'fct_episodes'
where
    (select count(*) from {{ ref('fct_episodes') }})
    != (select count(*) from {{ ref('stg_imdb__title_episode') }})
