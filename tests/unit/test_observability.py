from imdb_ducklake.observability import configure_logging


def test_configure_logging_sets_process_level_and_format(monkeypatch) -> None:
    configured: dict[str, object] = {}
    monkeypatch.setattr("logging.basicConfig", lambda **values: configured.update(values))

    configure_logging("DEBUG")

    assert configured == {
        "level": "DEBUG",
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        "force": True,
    }
