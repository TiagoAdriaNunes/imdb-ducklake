with row_counts as (
    select
        'title_akas' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_akas') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_akas') }}) as staging_count
    union all
    select
        'title_basics' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_basics') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_basics') }}) as staging_count
    union all
    select
        'title_crew' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_crew') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_crew') }}) as staging_count
    union all
    select
        'title_episode' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_episode') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_episode') }}) as staging_count
    union all
    select
        'title_principals' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_principals') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_principals') }}) as staging_count
    union all
    select
        'title_ratings' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'title_ratings') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__title_ratings') }}) as staging_count
    union all
    select
        'name_basics' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'name_basics') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__name_basics') }}) as staging_count
    union all
    select
        'ingestion_files' as relation_name,
        (select count(*) from {{ source('imdb_raw', 'ingestion_files') }}) as raw_count,
        (select count(*) from {{ ref('stg_imdb__ingestion_files') }}) as staging_count
)

select *
from row_counts
where raw_count != staging_count
