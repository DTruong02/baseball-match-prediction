"""YAML-backed training configuration for model runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from baseball_analyze.models.training_filters import coerce_optional_iso_date


class HyperparametersConfig(BaseModel):
    calibrate: bool = False
    class_weight: str = "balanced"
    c_grid: list[float] = Field(default_factory=lambda: [1.0])

    @property
    def class_weight_sklearn(self) -> Optional[str]:
        if self.class_weight.strip().lower() in ("none", "null", "0"):
            return None
        return "balanced"


class TrainingConfig(BaseModel):
    seasons: list[int] = Field(default_factory=lambda: [2022, 2023])
    val_seasons: list[int] = Field(default_factory=list)
    through_date: Optional[str] = None
    val_from_date: Optional[str] = None
    out: Path = Path("artifacts/model.joblib")
    test_size: float = 0.25
    max_games: Optional[int] = None
    log_csv: Path = Path("artifacts/training_log.csv")
    hyperparameters: HyperparametersConfig = Field(default_factory=HyperparametersConfig)
    random_state: int = 42
    cache_dir: Optional[Path] = None

    @field_validator("out", "log_csv", mode="before")
    @classmethod
    def _coerce_required_path(cls, value: Any) -> Any:
        if isinstance(value, str):
            return Path(value)
        return value

    @field_validator("cache_dir", mode="before")
    @classmethod
    def _coerce_optional_path(cls, value: Any) -> Any:
        if value is None or isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        return value

    @field_validator("through_date", "val_from_date", mode="before")
    @classmethod
    def _coerce_optional_date(cls, value: Any, info: ValidationInfo) -> Any:
        return coerce_optional_iso_date(value, field_name=info.field_name)


def _parse_season_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _parse_c_grid(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def load_training_config(path: Path) -> TrainingConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Training config must be a YAML mapping, got {type(raw).__name__}")
    return TrainingConfig.model_validate(raw)


def apply_cli_overrides(
    base: TrainingConfig,
    *,
    seasons: Optional[str] = None,
    val_seasons: Optional[str] = None,
    through_date: Optional[str] = None,
    val_from_date: Optional[str] = None,
    out: Optional[Path] = None,
    test_size: Optional[float] = None,
    max_games: Optional[int] = None,
    log_csv: Optional[Path] = None,
    tune_c: Optional[str] = None,
    class_weight: Optional[str] = None,
    calibrate: Optional[bool] = None,
    cache_dir: Optional[Path] = None,
    random_state: Optional[int] = None,
) -> TrainingConfig:
    """Return a copy of ``base`` with non-None CLI values applied."""
    data = base.model_dump()
    hyperparameters = dict(data["hyperparameters"])

    if seasons is not None:
        data["seasons"] = _parse_season_list(seasons)
    if val_seasons is not None:
        data["val_seasons"] = _parse_season_list(val_seasons)
    if through_date is not None:
        data["through_date"] = through_date
    if val_from_date is not None:
        data["val_from_date"] = val_from_date
    if out is not None:
        data["out"] = out
    if test_size is not None:
        data["test_size"] = test_size
    if max_games is not None:
        data["max_games"] = max_games
    if log_csv is not None:
        data["log_csv"] = log_csv
    if tune_c is not None:
        hyperparameters["c_grid"] = _parse_c_grid(tune_c)
    if class_weight is not None:
        hyperparameters["class_weight"] = class_weight
    if calibrate is not None:
        hyperparameters["calibrate"] = calibrate
    if cache_dir is not None:
        data["cache_dir"] = cache_dir
    if random_state is not None:
        data["random_state"] = random_state

    data["hyperparameters"] = hyperparameters
    return TrainingConfig.model_validate(data)
