-- Cast credits for app_title_search's titles only. Sorted by tconst (app.yml), so each file
-- covers a distinct tconst range and the catalog's file stats prune remote lookups.
select
    filmography.tconst,
    filmography.nconst,
    filmography.primary_name,
    filmography.category,
    filmography.ordering,
    array_to_string(filmography.characters, ', ') as characters_text
from {{ ref('mart_person_filmography') }} as filmography
semi join {{ ref('app_title_search') }} as titles
    on filmography.tconst = titles.tconst
where filmography.category in ('actor', 'actress', 'self')
