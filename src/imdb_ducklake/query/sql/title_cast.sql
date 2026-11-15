select -- noqa: ST06
    nconst as "IMDb Person ID",
    coalesce(primary_name, nconst) as "Name",
    category as "Role",
    characters_text as "Characters"
from app.app_title_cast
where tconst = $tconst -- noqa: PRS, LXR
order by ordering, nconst
