select
    primary_title as "Title",
    nconst as "IMDb Person ID",
    coalesce(primary_name, nconst) as "Name",
    category as "Role",
    array_to_string(characters, ', ') as "Characters"
from marts.mart_person_filmography
where
    tconst = $tconst
    and category in ('actor', 'actress', 'self')
order by ordering, nconst
