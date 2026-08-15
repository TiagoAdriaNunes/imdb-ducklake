from imdb_ducklake.datasets import BASE_URL, DATASETS, DATASETS_BY_TABLE


def test_registry_contains_all_seven_unique_imdb_datasets() -> None:
    assert len(DATASETS) == 7
    assert len({dataset.file_name for dataset in DATASETS}) == 7
    assert len(DATASETS_BY_TABLE) == 7

    for dataset in DATASETS:
        assert DATASETS_BY_TABLE[dataset.table_name] is dataset
        assert dataset.url == f"{BASE_URL}/{dataset.file_name}"
