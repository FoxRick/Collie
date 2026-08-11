"""Validated renderer and controller for the preserved Collie v2 desktop pet.

The production art is an 8 x 11, 192 x 208 atlas plus two horizontal
strips. This module keeps state selection separate from image extraction so
the glasses strip can only ever be reached by deep work.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import atan2, hypot, pi
from pathlib import Path
from typing import Literal

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the pet cannot render without Pillow.
    Image = None

CELL_W = 192
CELL_H = 208
ATLAS_COLUMNS = 8
ATLAS_ROWS = 11
DEEP_WORK_THRESHOLD_MS = 11_000
CLICK_REACTION_MS = 850
CLICK_COOLDOWN_MS = 1_000
COMPLETION_MS = 2_400
POINTER_STEP_MS = 72

BaseState = Literal["idle", "working", "review", "waiting", "error"]
VisualState = Literal[
    "idle",
    "walk_right",
    "walk_left",
    "working",
    "review",
    "waiting",
    "error",
    "completion",
    "click_reaction",
    "deep_work_glasses",
    "pointer_look",
]
AssetKind = Literal["atlas", "glasses", "bone"]

ASSET_DIR = Path(__file__).with_name("assets")
ASSET_FILES: dict[AssetKind, str] = {
    "atlas": "collie-v2-full-body-atlas.webp",
    "glasses": "collie-deep-work-glasses-full-body.webp",
    "bone": "collie-bone-completion-full-body.webp",
}
ASSET_SHA256: dict[AssetKind, str] = {
    "atlas": "a20457b4c5bca2feef1a88f7f48e3f5446d2a35a018e663c8d66b7f08b42903f",
    "glasses": "fbcdeb68749979ccdd9e75ce1d3e0d4f2e68437b5e03c3b9b0943f37721f26ac",
    "bone": "cc0564da0a5aca6b86605e50c5ff6dd25cc5ef70177ab9f9c1d5aa5d488d753e",
}


@dataclass(frozen=True)
class FrameSequence:
    """Frames and timings for one contract-approved visual state."""

    asset: AssetKind
    cells: tuple[int, ...]
    durations_ms: tuple[int, ...]


def _sequence(asset: AssetKind, start: int, durations: tuple[int, ...]) -> FrameSequence:
    return FrameSequence(asset, tuple(range(start, start + len(durations))), durations)


FRAME_SEQUENCES: dict[VisualState, FrameSequence] = {
    "idle": _sequence("atlas", 0, (280, 110, 110, 140, 140, 320)),
    "walk_right": _sequence("atlas", 8, (120, 120, 120, 120, 120, 120, 120, 220)),
    "walk_left": _sequence("atlas", 16, (120, 120, 120, 120, 120, 120, 120, 220)),
    "working": _sequence("atlas", 56, (120, 120, 120, 120, 120, 220)),
    "review": _sequence("atlas", 64, (150, 150, 150, 150, 150, 280)),
    "waiting": _sequence("atlas", 48, (150, 150, 150, 150, 150, 260)),
    "error": _sequence("atlas", 40, (140, 140, 140, 140, 140, 140, 140, 240)),
    "completion": _sequence("bone", 0, (180, 220, 260, 420)),
    "click_reaction": _sequence("atlas", 24, (140, 140, 140, 280)),
    "deep_work_glasses": _sequence("glasses", 0, (320, 260, 300, 300, 260, 420)),
    "pointer_look": _sequence("atlas", 72, (360,) * 16),
}


class V2AssetError(RuntimeError):
    """The desktop pet's preserved production asset bundle is unavailable."""


def quantize_pointer_direction(delta_x: float, delta_y: float, dead_zone: float = 16) -> int | None:
    """Quantize a screen-coordinate pointer vector clockwise from up."""
    if hypot(delta_x, delta_y) <= dead_zone:
        return None
    return (round((atan2(delta_x, -delta_y) / (2 * pi)) * 16) + 16) % 16


def asset_paths(asset_dir: Path = ASSET_DIR) -> dict[AssetKind, Path]:
    """Return only byte-verified production assets with their expected geometry."""
    paths: dict[AssetKind, Path] = {}
    expected_sizes = {
        "atlas": (CELL_W * ATLAS_COLUMNS, CELL_H * ATLAS_ROWS),
        "glasses": (CELL_W * 6, CELL_H),
        "bone": (CELL_W * 4, CELL_H),
    }
    for kind, filename in ASSET_FILES.items():
        path = asset_dir / filename
        if not path.is_file():
            raise V2AssetError(f"Missing Collie v2 {kind} asset: {path}")
        digest = sha256(path.read_bytes()).hexdigest()
        if digest != ASSET_SHA256[kind]:
            raise V2AssetError(
                f"Collie v2 {kind} asset hash did not match the preservation manifest"
            )
        if Image is None:
            raise V2AssetError("Pillow is required to load Collie v2 assets")
        with Image.open(path) as source:
            if source.mode != "RGBA" or source.size != expected_sizes[kind]:
                raise V2AssetError(
                    f"Collie v2 {kind} asset geometry must be {expected_sizes[kind]} RGBA, got "
                    f"{source.size} {source.mode}"
                )
        paths[kind] = path
    return paths


class V2SpriteRenderer:
    """Extract unmodified v2 cells from the approved atlas and strips."""

    def __init__(self, asset_dir: Path = ASSET_DIR) -> None:
        paths = asset_paths(asset_dir)
        self._sources = {kind: Image.open(path).convert("RGBA") for kind, path in paths.items()}

    def frames_for(self, state: VisualState) -> list[Image.Image]:
        sequence = FRAME_SEQUENCES[state]
        return [self._cell(sequence.asset, cell) for cell in sequence.cells]

    def _cell(self, asset: AssetKind, cell: int) -> Image.Image:
        source = self._sources[asset]
        if asset == "atlas":
            x = (cell % ATLAS_COLUMNS) * CELL_W
            y = (cell // ATLAS_COLUMNS) * CELL_H
        else:
            x = cell * CELL_W
            y = 0
        return source.crop((x, y, x + CELL_W, y + CELL_H))


@dataclass(frozen=True)
class AnimationSnapshot:
    base_state: BaseState
    state: VisualState
    state_changed_at: int
    direction: int | None


class V2AnimationController:
    """Pure state machine enforcing the v2 state and glasses contracts."""

    def __init__(self, initial_state: BaseState = "idle", now: int = 0) -> None:
        self.base_state = initial_state
        self.base_changed_at = now
        self.work_started_at: int | None = now if initial_state == "working" else None
        self.transient: Literal["completion", "click_reaction"] | None = None
        self.transient_started_at = 0
        self.transient_ends_at = 0
        self.click_cooldown_ends_at = 0
        self.pointer_direction: int | None = None
        self.pointer_target: int | None = None
        self.last_pointer_step_at = 0
        self.directional_motion: Literal["walk_right", "walk_left"] | None = None
        self.directional_motion_started_at = 0

    def set_base_state(self, state: BaseState, now: int) -> None:
        self.directional_motion = None
        self.directional_motion_started_at = 0
        if state == self.base_state:
            return
        self.base_state = state
        self.base_changed_at = now
        self.work_started_at = now if state == "working" else None
        if state in {"waiting", "error"}:
            self._clear_transient()

    def complete(self, now: int) -> None:
        self.directional_motion = None
        self.directional_motion_started_at = 0
        self.base_state = "idle"
        self.base_changed_at = now
        self.work_started_at = None
        self.transient = "completion"
        self.transient_started_at = now
        self.transient_ends_at = now + COMPLETION_MS

    def trigger_click(self, now: int, direction: int | None = None) -> bool:
        if self.base_state in {"waiting", "error"} or now < self.click_cooldown_ends_at:
            return False
        self.directional_motion = None
        self.directional_motion_started_at = 0
        if direction is not None:
            self.set_pointer_target(direction, now)
        self.transient = "click_reaction"
        self.transient_started_at = now
        self.transient_ends_at = now + CLICK_REACTION_MS
        self.click_cooldown_ends_at = now + CLICK_COOLDOWN_MS
        return True

    def start_directional_motion(self, state: Literal["walk_right", "walk_left"], now: int) -> bool:
        """Start a bounded desktop walk without masquerading as engine work."""
        if self.base_state != "idle" or self.transient is not None:
            return False
        self.directional_motion = state
        self.directional_motion_started_at = now
        self.pointer_direction = None
        self.pointer_target = None
        return True

    def set_pointer_target(self, direction: int | None, now: int) -> None:
        self.pointer_target = direction
        if direction is None:
            self.pointer_direction = None
            return
        if not 0 <= direction < 16:
            raise ValueError("Pointer direction must be in the v2 range 0..15")
        if self.pointer_direction is None:
            self.pointer_direction = direction
            self.last_pointer_step_at = now

    def snapshot(self, now: int, reduced_motion: bool = False) -> AnimationSnapshot:
        self._expire_transient(now)
        self._advance_pointer(now)
        if self.base_state in {"waiting", "error"}:
            return AnimationSnapshot(self.base_state, self.base_state, self.base_changed_at, None)
        if self.transient is not None:
            return AnimationSnapshot(
                self.base_state, self.transient, self.transient_started_at, self.pointer_direction
            )
        if self._is_deep_work(now):
            return AnimationSnapshot(
                self.base_state,
                "deep_work_glasses",
                (self.work_started_at if self.work_started_at is not None else now)
                + DEEP_WORK_THRESHOLD_MS,
                None,
            )
        if self.base_state in {"working", "review"}:
            return AnimationSnapshot(self.base_state, self.base_state, self.base_changed_at, None)
        if self.directional_motion is not None:
            return AnimationSnapshot(
                self.base_state,
                self.directional_motion,
                self.directional_motion_started_at,
                None,
            )
        if not reduced_motion and self.pointer_direction is not None:
            return AnimationSnapshot(
                self.base_state, "pointer_look", self.last_pointer_step_at, self.pointer_direction
            )
        return AnimationSnapshot(self.base_state, "idle", self.base_changed_at, None)

    def _is_deep_work(self, now: int) -> bool:
        return (
            self.base_state == "working"
            and self.work_started_at is not None
            and (now - self.work_started_at >= DEEP_WORK_THRESHOLD_MS)
        )

    def _expire_transient(self, now: int) -> None:
        if self.transient is not None and now >= self.transient_ends_at:
            self._clear_transient()

    def _clear_transient(self) -> None:
        self.transient = None
        self.transient_started_at = 0
        self.transient_ends_at = 0

    def _advance_pointer(self, now: int) -> None:
        if (
            self.pointer_direction is None
            or self.pointer_target is None
            or self.pointer_direction == self.pointer_target
            or now - self.last_pointer_step_at < POINTER_STEP_MS
        ):
            return
        clockwise = (self.pointer_target - self.pointer_direction + 16) % 16
        self.pointer_direction = (self.pointer_direction + (1 if clockwise <= 8 else -1)) % 16
        self.last_pointer_step_at = now
