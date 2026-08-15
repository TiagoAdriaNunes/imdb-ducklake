select
    cast("tconst" as varchar) as tconst,
    cast(nullif("averageRating", '\N') as double) as average_rating,
    cast(nullif("numVotes", '\N') as bigint) as num_votes,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'title_ratings') }}
