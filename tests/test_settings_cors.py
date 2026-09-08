from baseball_backend.settings import Settings


def test_cors_origin_list_splits_and_strips() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins="https://baseball.example.com, http://localhost:3000 ,",
    )
    assert settings.cors_origin_list() == [
        "https://baseball.example.com",
        "http://localhost:3000",
    ]


def test_cors_origin_list_empty_string() -> None:
    settings = Settings(_env_file=None, cors_origins="")
    assert settings.cors_origin_list() == []
