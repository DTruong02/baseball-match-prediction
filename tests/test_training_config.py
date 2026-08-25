from pathlib import Path

import pytest

from baseball_analyze.models.training_config import (
    TrainingConfig,
    apply_cli_overrides,
    load_training_config,
)


def test_load_logistic_regression_yaml():
    path = Path("configs/logistic_regression.yaml")
    cfg = load_training_config(path)

    assert cfg.seasons == [2022, 2023]
    assert cfg.val_seasons == []
    assert cfg.max_games is None
    assert cfg.test_size == 0.25
    assert cfg.out == Path("artifacts/model.joblib")
    assert cfg.log_csv == Path("artifacts/training_log.csv")
    assert cfg.random_state == 42
    assert cfg.cache_dir is None
    assert cfg.hyperparameters.calibrate is False
    assert cfg.hyperparameters.class_weight == "balanced"
    assert cfg.hyperparameters.c_grid == [0.05, 0.1, 0.5, 1.0, 5.0, 10.0]


def test_training_config_defaults_match_legacy_cli():
    cfg = TrainingConfig()

    assert cfg.seasons == [2022, 2023]
    assert cfg.val_seasons == []
    assert cfg.out == Path("artifacts/model.joblib")
    assert cfg.test_size == 0.25
    assert cfg.max_games is None
    assert cfg.hyperparameters.c_grid == [1.0]
    assert cfg.hyperparameters.class_weight_sklearn == "balanced"
    assert cfg.hyperparameters.calibrate is False
    assert cfg.random_state == 42


def test_apply_cli_overrides():
    base = load_training_config(Path("configs/logistic_regression.yaml"))
    cfg = apply_cli_overrides(
        base,
        seasons="2023,2024",
        val_seasons="2024",
        max_games=250,
        tune_c="0.5,1",
        class_weight="none",
        calibrate=True,
        out=Path("artifacts/custom.joblib"),
        test_size=0.2,
        random_state=7,
    )

    assert cfg.seasons == [2023, 2024]
    assert cfg.val_seasons == [2024]
    assert cfg.max_games == 250
    assert cfg.hyperparameters.c_grid == [0.5, 1.0]
    assert cfg.hyperparameters.class_weight == "none"
    assert cfg.hyperparameters.class_weight_sklearn is None
    assert cfg.hyperparameters.calibrate is True
    assert cfg.out == Path("artifacts/custom.joblib")
    assert cfg.test_size == 0.2
    assert cfg.random_state == 7
    assert cfg.log_csv == Path("artifacts/training_log.csv")


def test_load_training_config_rejects_non_mapping(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n- 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML mapping"):
        load_training_config(bad)
