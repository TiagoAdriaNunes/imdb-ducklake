"""The authoritative registry of IMDb source datasets."""

from dataclasses import dataclass

BASE_URL = "https://datasets.imdbws.com"


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Immutable contract for one IMDb source file."""

    name: str
    file_name: str
    table_name: str
    headers: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.file_name}"


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        name="alternate titles",
        file_name="title.akas.tsv.gz",
        table_name="title_akas",
        headers=(
            "titleId",
            "ordering",
            "title",
            "region",
            "language",
            "types",
            "attributes",
            "isOriginalTitle",
        ),
    ),
    DatasetSpec(
        name="title basics",
        file_name="title.basics.tsv.gz",
        table_name="title_basics",
        headers=(
            "tconst",
            "titleType",
            "primaryTitle",
            "originalTitle",
            "isAdult",
            "startYear",
            "endYear",
            "runtimeMinutes",
            "genres",
        ),
    ),
    DatasetSpec(
        name="title crew",
        file_name="title.crew.tsv.gz",
        table_name="title_crew",
        headers=("tconst", "directors", "writers"),
    ),
    DatasetSpec(
        name="episodes",
        file_name="title.episode.tsv.gz",
        table_name="title_episode",
        headers=("tconst", "parentTconst", "seasonNumber", "episodeNumber"),
    ),
    DatasetSpec(
        name="principal credits",
        file_name="title.principals.tsv.gz",
        table_name="title_principals",
        headers=("tconst", "ordering", "nconst", "category", "job", "characters"),
    ),
    DatasetSpec(
        name="title ratings",
        file_name="title.ratings.tsv.gz",
        table_name="title_ratings",
        headers=("tconst", "averageRating", "numVotes"),
    ),
    DatasetSpec(
        name="people",
        file_name="name.basics.tsv.gz",
        table_name="name_basics",
        headers=(
            "nconst",
            "primaryName",
            "birthYear",
            "deathYear",
            "primaryProfession",
            "knownForTitles",
        ),
    ),
)

DATASETS_BY_TABLE = {dataset.table_name: dataset for dataset in DATASETS}
