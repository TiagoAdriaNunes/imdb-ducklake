select -- noqa: ST06
    primary_title as "Title",
    nconst as "IMDb Person ID",
    coalesce(primary_name, nconst) as "Name",
    category as "Role",
    array_to_string(characters, ', ') as "Characters"
from marts.mart_title_cast
where tconst = $tconst -- noqa: PRS, LXR
order by ordering, nconst
