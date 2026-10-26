"""Bounded public computer command shared by IPC and agent tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ComputerCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal[
        "status",
        "apps",
        "windows",
        "observe",
        "desktop",
        "click",
        "type",
        "hotkey",
        "scroll",
        "drag",
        "stop",
    ]
    pid: int | None = Field(default=None, gt=0)
    window_id: int | None = Field(default=None, gt=0)
    observation_id: str | None = Field(default=None, max_length=64)
    element_token: str | None = Field(default=None, max_length=64)
    query: str = Field(default="", max_length=160)
    max_elements: int = Field(default=120, ge=1, le=250)
    max_depth: int = Field(default=8, ge=1, le=16)
    screenshot: bool = False
    delivery_mode: Literal["background", "foreground"] = "background"
    scope: Literal["window", "desktop"] = "window"
    text: str | None = Field(default=None, max_length=2000)
    keys: list[str] = Field(default_factory=list, max_length=4)
    direction: Literal["up", "down", "left", "right"] = "down"
    amount: int = Field(default=3, ge=1, le=10)
    x: float | None = Field(default=None, ge=0, le=32768)
    y: float | None = Field(default=None, ge=0, le=32768)
    to_x: float | None = Field(default=None, ge=0, le=32768)
    to_y: float | None = Field(default=None, ge=0, le=32768)
