with row_counts as (
    select
        'title_akas' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_akas') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_akas') }}) as staging_count
    union all
    select
        'title_basics',
        (select count(*) from {{ source('imdb_raw', 'title_basics') }}),
        (select count(*) from {{ ref('stg_imdb__title_basics') }})
    union all
    select
        'title_crew',
        (select count(*) from {{ source('imdb_raw', 'title_crew') }}),
        (select count(*) from {{ ref('stg_imdb__title_crew') }})
    union all
    select
        'title_episode',
        (select count(*) from {{ source('imdb_raw', 'title_episode') }}),
        (select count(*) from {{ ref('stg_imdb__title_episode') }})
    union all
    select
        'title_principals',
        (select count(*) from {{ source('imdb_raw', 'title_principals') }}),
        (select count(*) from {{ ref('stg_imdb__title_principals') }})
    union all
    select
        'title_ratings',
        (select count(*) from {{ source('imdb_raw', 'title_ratings') }}),
        (select count(*) from {{ ref('stg_imdb__title_ratings') }})
    union all
    select
        'name_basics',
        (select count(*) from {{ source('imdb_raw', 'name_basics') }}),
        (select count(*) from {{ ref('stg_imdb__name_basics') }})
    union all
    select
        'ingestion_files',
        (select count(*) from {{ source('imdb_raw', 'ingestion_files') }}),
        (select count(*) from {{ ref('stg_imdb__ingestion_files') }})
)

select *
from row_counts
where raw_count != staging_count
