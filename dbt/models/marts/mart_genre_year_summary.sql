select
    titles.start_year,
    titles.title_type,
    genres.genre,
    count(*)::bigint as title_count,
    count(ratings.average_rating)::bigint as rated_title_count,
    avg(ratings.average_rating) as average_rating,
    sum(coalesce(ratings.num_votes, 0))::hugeint as total_votes
from {{ ref('bridge_title_genres') }} as genres
inner join {{ ref('dim_titles') }} as titles using (tconst)
left join {{ ref('fct_title_ratings') }} as ratings using (tconst)
group by titles.start_year, titles.title_type, genres.genre
