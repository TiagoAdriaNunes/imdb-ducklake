select
    episodes.parent_tconst as series_tconst,
    series.primary_title as series_title,
    episodes.tconst as episode_tconst,
    episode.primary_title as episode_title,
    episodes.season_number,
    episodes.episode_number,
    episode.start_year,
    ratings.average_rating,
    ratings.num_votes,
    episodes.dlt_load_id
from {{ ref('fct_episodes') }} as episodes
inner join {{ ref('dim_titles') }} as series on episodes.parent_tconst = series.tconst
inner join {{ ref('dim_titles') }} as episode on episodes.tconst = episode.tconst
left join {{ ref('fct_title_ratings') }} as ratings on episodes.tconst = ratings.tconst
