"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str


class TeamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abbreviation: str
    name: str
    city: Optional[str] = None


class PlayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str


class GameRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_pk: int
    game_date: date
    season: int
    status: str
    detailed_state: str
    home_team: TeamRead
    away_team: TeamRead
    venue_id: Optional[int] = None
    venue_name: Optional[str] = None
    home_probable_pitcher: Optional[PlayerRead] = None
    away_probable_pitcher: Optional[PlayerRead] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    winner: Optional[str] = None


class ScheduleSyncResponse(BaseModel):
    date: date
    games_synced: int
