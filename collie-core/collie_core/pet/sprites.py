"""
Border Collie sprite renderer — draws a programmatic collie in multiple
animation states, inspired by the Codex V2 pet contract (8 animation rows,
192x208 cells per frame).

Each state returns a list of Pillow Image frames.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

CELL_W = 192
CELL_H = 208
TRANSPARENT = (0, 0, 0, 0)

# Border Collie palette
BLACK = (30, 30, 35, 255)
WHITE = (240, 240, 245, 255)
DARK_GREY = (55, 55, 60, 255)
EYE_AMBER = (180, 140, 60, 255)
EYE_DARK = (40, 30, 10, 255)
NOSE_COLOR = (20, 20, 25, 255)
TONGUE_PINK = (230, 130, 130, 255)
COLLAR_BLUE = (40, 80, 180, 255)
COLLAR_GOLD = (200, 170, 50, 255)

# Six user-facing animation states. Walking left is an internal render variant,
# not a separate behavior exposed in Settings or the context menu.
ANIM_STATES = [
    "idle",
    "working",
    "walk",
    "sleep",
    "happy",
    "concerned",
]
RENDER_STATES = [*ANIM_STATES, "walk_left"]

FRAME_DURATIONS: dict[str, list[int]] = {
    "idle": [520, 180, 520, 900],
    "working": [260, 180, 260, 520],
    "walk": [150, 150, 150, 240],
    "walk_left": [150, 150, 150, 240],
    "sleep": [900, 1100],
    "happy": [180, 160, 220, 520],
    "concerned": [420, 220, 520, 800],
}


def _draw_collie_base(
    draw: ImageDraw.ImageDraw,
    body_x: int,
    body_y: int,
    facing_right: bool = True,
    scale: float = 1.0,
) -> None:
    """Draw the core Border Collie body components at a given position."""

    def sc(v: float) -> int:
        return round(v * scale)

    fx = 1 if facing_right else -1

    # Body (oval)
    bw, bh = sc(70), sc(40)
    draw.ellipse(
        [body_x - bw // 2, body_y - bh // 2, body_x + bw // 2, body_y + bh // 2],
        fill=BLACK,
    )

    # White chest / belly patch
    chest_w, chest_h = sc(36), sc(30)
    chest_cx = body_x + fx * sc(5)
    draw.ellipse(
        [
            chest_cx - chest_w // 2,
            body_y - chest_h // 4,
            chest_cx + chest_w // 2,
            body_y + chest_h,
        ],
        fill=WHITE,
    )

    # Head
    head_r = sc(22)
    head_cx = body_x + fx * sc(40)
    head_cy = body_y - sc(18)
    draw.ellipse(
        [
            head_cx - head_r,
            head_cy - head_r,
            head_cx + head_r,
            head_cy + head_r,
        ],
        fill=BLACK,
    )

    # White blaze on face
    blaze_w, blaze_h = sc(10), sc(28)
    draw.ellipse(
        [
            head_cx - blaze_w // 2,
            head_cy - blaze_h // 2,
            head_cx + blaze_w // 2,
            head_cy + blaze_h // 2 - sc(2),
        ],
        fill=WHITE,
    )

    # Snout
    snout_w, snout_h = sc(18), sc(14)
    snout_cx = head_cx + fx * sc(18)
    snout_cy = head_cy + sc(4)
    draw.ellipse(
        [
            snout_cx - snout_w // 2,
            snout_cy - snout_h // 2,
            snout_cx + snout_w // 2,
            snout_cy + snout_h // 2,
        ],
        fill=BLACK,
    )
    # Lower snout white
    draw.ellipse(
        [
            snout_cx - sc(8),
            snout_cy,
            snout_cx + sc(8),
            snout_cy + snout_h // 2,
        ],
        fill=WHITE,
    )

    # Nose
    nose_r = sc(4)
    nose_cx = snout_cx + fx * sc(7)
    nose_cy = snout_cy - sc(2)
    draw.ellipse(
        [
            nose_cx - nose_r,
            nose_cy - nose_r,
            nose_cx + nose_r,
            nose_cy + nose_r,
        ],
        fill=NOSE_COLOR,
    )

    # Eyes
    eye_r = sc(5)
    for e_off in [-sc(7), sc(7)]:
        ex = head_cx + e_off
        ey = head_cy - sc(2)
        # Amber iris
        draw.ellipse(
            [ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r],
            fill=EYE_AMBER,
        )
        # Pupil
        pupil_r = sc(2)
        draw.ellipse(
            [
                ex + fx * sc(1) - pupil_r,
                ey - pupil_r,
                ex + fx * sc(1) + pupil_r,
                ey + pupil_r,
            ],
            fill=EYE_DARK,
        )

    # Ears
    ear_w, _ = sc(10), sc(22)
    for e_off, ear_dir in [(-sc(14), -sc(8)), (sc(14), sc(6))]:
        ear_tip_x = head_cx + e_off + ear_dir
        ear_tip_y = head_cy - head_r - sc(6)
        ear_base_cx = head_cx + e_off
        ear_base_cy = head_cy - head_r + sc(4)
        draw.polygon(
            [
                (ear_base_cx - ear_w // 2, ear_base_cy),
                (ear_base_cx + ear_w // 2, ear_base_cy),
                (ear_tip_x, ear_tip_y),
            ],
            fill=BLACK if e_off < 0 else BLACK,
        )
        # Inner ear pink
        inner_w, inner_h = sc(5), sc(12)
        mid_x = (ear_base_cx + ear_tip_x) // 2
        mid_y = (ear_base_cy + ear_tip_y) // 2
        draw.polygon(
            [
                (mid_x - inner_w // 2, mid_y + sc(2)),
                (mid_x + inner_w // 2, mid_y + sc(2)),
                (mid_x + (ear_dir // abs(ear_dir) if ear_dir else 0) * sc(2), mid_y - inner_h // 2),
            ],
            fill=(210, 170, 160, 255),
        )

    # Tail (bushy, with white tip)
    tail_base_x = body_x - fx * sc(30)
    tail_base_y = body_y - sc(8)
    tail_tip_x = tail_base_x - fx * sc(20)
    tail_tip_y = tail_base_y + sc(12)
    tail_mid_x = (tail_base_x + tail_tip_x) // 2 - fx * sc(6)
    tail_mid_y = (tail_base_y + tail_tip_y) // 2 - sc(10)
    # Black base
    draw.ellipse(
        [tail_mid_x - sc(12), tail_mid_y - sc(8), tail_mid_x + sc(12), tail_mid_y + sc(8)],
        fill=BLACK,
    )
    # White tip
    draw.ellipse(
        [tail_tip_x - sc(8), tail_tip_y - sc(6), tail_tip_x + sc(8), tail_tip_y + sc(6)],
        fill=WHITE,
    )

    # Legs (4 short legs)
    leg_w, leg_h = sc(12), sc(28)
    leg_bottom = body_y + bh // 2 + sc(6)
    for lx_off in [-sc(18), sc(8), sc(18), sc(-8)]:
        lx = body_x + lx_off
        draw.rectangle(
            [lx - leg_w // 2, leg_bottom - leg_h, lx + leg_w // 2, leg_bottom],
            fill=BLACK,
        )
        # White paws
        draw.ellipse(
            [lx - sc(7), leg_bottom - sc(8), lx + sc(7), leg_bottom + sc(2)],
            fill=WHITE,
        )

    # Collar (the "harness" stripe element)
    collar_y = body_y - sc(25)
    draw.rectangle(
        [body_x - sc(24), collar_y - sc(4), body_x + sc(24), collar_y + sc(3)],
        fill=COLLAR_BLUE,
    )
    # Gold tag
    draw.circle((body_x, collar_y + sc(8)), sc(5), fill=COLLAR_GOLD)


def _create_frame() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (CELL_W, CELL_H), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    return img, draw


def generate_idle() -> list[Image.Image]:
    """Breathing idle loop with occasional blink (6 frames)."""
    frames = []
    # 6 frames: expand, contract, neutral x4
    body_positions = [
        (CELL_W // 2 + 2, CELL_H // 2 + 30, True, 1.0),  # frame 0: neutral
        (CELL_W // 2 + 2, CELL_H // 2 + 28, True, 1.02),  # frame 1: inhale
        (CELL_W // 2 + 2, CELL_H // 2 + 32, True, 0.98),  # frame 2: exhale
        (CELL_W // 2 + 2, CELL_H // 2 + 30, True, 1.0),  # frame 3: neutral
        (CELL_W // 2 + 2, CELL_H // 2 + 30, True, 1.0),  # frame 4: blink closed
        (CELL_W // 2 + 2, CELL_H // 2 + 30, True, 1.0),  # frame 5: neutral
    ]
    for i, (bx, by, facing, scl) in enumerate(body_positions):
        img, draw = _create_frame()
        _draw_collie_base(draw, bx, by, facing, scl)
        # On frame 4, draw closed eyes instead (overlay)
        if i == 4:
            # Overwrite eyes with closed slits
            head_x = bx + (1 if facing else -1) * round(40 * scl)
            head_y = by - round(18 * scl)
            for e_off in [-round(7 * scl), round(7 * scl)]:
                ex = head_x + e_off
                ey = head_y - round(2 * scl)
                draw.line(
                    [ex - round(4 * scl), ey, ex + round(4 * scl), ey],
                    fill=EYE_DARK,
                    width=2,
                )
        frames.append(img)
    return frames


def generate_walk_right() -> list[Image.Image]:
    """Walking to the right (6 frames)."""
    frames = []
    leg_phases = [0, 0.25, 0.5, 0.75, 0.5, 0.25]
    for _i, phase in enumerate(leg_phases):
        img, draw = _create_frame()
        bx = CELL_W // 2 + round(math.sin(phase * math.pi * 2) * 4)
        by = CELL_H // 2 + 30 + round(abs(math.cos(phase * math.pi * 2)) * 3)
        _draw_collie_base(draw, bx, by, True, 1.0)
        frames.append(img)
    return frames


def generate_walk_left() -> list[Image.Image]:
    """Walking to the left (6 frames)."""
    frames = []
    leg_phases = [0, 0.25, 0.5, 0.75, 0.5, 0.25]
    for _i, phase in enumerate(leg_phases):
        img, draw = _create_frame()
        bx = CELL_W // 2 - round(math.sin(phase * math.pi * 2) * 4)
        by = CELL_H // 2 + 30 + round(abs(math.cos(phase * math.pi * 2)) * 3)
        _draw_collie_base(draw, bx, by, False, 1.0)
        frames.append(img)
    return frames


def generate_sit() -> list[Image.Image]:
    """Sitting pose, slight breathing (4 frames)."""
    frames = []
    for i in range(4):
        img, draw = _create_frame()
        bx = CELL_W // 2 + 2
        by = CELL_H // 2 + 50  # Lower = sitting
        scl = 1.0 + (0.02 if i == 1 else 0)
        _draw_collie_base(draw, bx, by, True, scl)
        frames.append(img)
    return frames


def generate_jump() -> list[Image.Image]:
    """Jump sequence: crouch, launch, peak, descend, land (5 frames)."""
    y_offsets = [10, -15, -35, -15, 10]
    scales = [0.92, 1.0, 1.05, 1.0, 0.92]
    frames = []
    for yo, scl in zip(y_offsets, scales, strict=True):
        img, draw = _create_frame()
        bx = CELL_W // 2 + 2
        by = CELL_H // 2 + 30 + yo
        _draw_collie_base(draw, bx, by, True, scl)
        frames.append(img)
    return frames


def generate_sleep() -> list[Image.Image]:
    """Sleeping pose, occasional Zzz motion (2 frames)."""
    frames = []
    for i in range(2):
        img, draw = _create_frame()
        # Draw collie lying down
        bx = CELL_W // 2 - 10
        by = CELL_H // 2 + 60
        # Scaled down, rotated-ish body (simplified as flatter ellipse)
        # We reuse the base but it'll look more like a curled ball
        _draw_collie_base(draw, bx, by, True, 0.65)
        # Zzz on frame 0
        if i == 0:
            for zi, (zx, zy) in enumerate(
                [
                    (CELL_W // 2 + 50, CELL_H // 2 + 10),
                    (CELL_W // 2 + 60, CELL_H // 2 - 5),
                    (CELL_W // 2 + 70, CELL_H // 2 - 20),
                ]
            ):
                draw.text((zx, zy), "Z", fill=(100, 150, 255, 255), font_size=10 + zi * 3)
        frames.append(img)
    return frames


def generate_wag() -> list[Image.Image]:
    """Tail wagging while standing (4 frames)."""
    frames = []
    # We vary the tail position — for simplicity we shift the whole
    # body slightly to suggest a wag, plus scale phase
    tail_phases = [0, 0.33, 0.66, 0.33]
    for phase in tail_phases:
        img, draw = _create_frame()
        bx = CELL_W // 2 + 2
        by = CELL_H // 2 + 30 + round(math.sin(phase * math.pi * 2) * 2)
        _draw_collie_base(draw, bx, by, True, 1.0 + math.sin(phase * math.pi * 2) * 0.01)
        frames.append(img)
    return frames


def generate_run() -> list[Image.Image]:
    """Fast running pose (6 frames)."""
    frames = []
    for i in range(6):
        img, draw = _create_frame()
        phase = i / 6.0
        bx = CELL_W // 2 + round(math.sin(phase * math.pi * 2) * 6)
        by = CELL_H // 2 + 30 + round(abs(math.cos(phase * math.pi * 2)) * 5)
        _draw_collie_base(draw, bx, by, True, 1.03)
        frames.append(img)
    return frames


def generate_happy() -> list[Image.Image]:
    """Happy bouncing / celebration (4 frames)."""
    y_offsets = [0, -18, 0, -10]
    frames = []
    for yo in y_offsets:
        img, draw = _create_frame()
        bx = CELL_W // 2 + 2
        by = CELL_H // 2 + 30 + yo
        _draw_collie_base(draw, bx, by, True, 1.05)
        frames.append(img)
    return frames


# Map state name -> generator function
STATE_GENERATORS = {
    "idle": generate_idle,
    "working": generate_sit,
    "walk": generate_walk_right,
    "walk_left": generate_walk_left,
    "sleep": generate_sleep,
    "happy": generate_happy,
    "concerned": generate_sit,
}


# High-fidelity raster mascot -------------------------------------------------
# The original geometric renderer remains above as a packaging-safe fallback.
# When the generated Collie art ships with the app, every state uses the same
# recognizable dog identity and only deterministic pose motion is applied.
ASSET_DIR = Path(__file__).with_name("assets")


def _asset_frames(
    asset_name: str,
    motion: list[tuple[int, int, float, float]],
    *,
    flip: bool = False,
) -> list[Image.Image]:
    path = ASSET_DIR / f"collie-{asset_name}.png"
    if not path.exists() or Image is None:
        return []
    source = Image.open(path).convert("RGBA")
    bbox = source.getchannel("A").getbbox()
    if bbox:
        source = source.crop(bbox)
    if flip:
        source = source.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    frames: list[Image.Image] = []
    for dx, dy, scale, angle in motion:
        frame = Image.new("RGBA", (CELL_W, CELL_H), TRANSPARENT)
        pet = source.copy()
        fit = min(174 / pet.width, 184 / pet.height) * scale
        pet = pet.resize(
            (max(1, round(pet.width * fit)), max(1, round(pet.height * fit))),
            Image.Resampling.LANCZOS,
        )
        if angle:
            pet = pet.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        x = (CELL_W - pet.width) // 2 + dx
        y = CELL_H - pet.height - 5 + dy
        frame.alpha_composite(pet, (x, y))
        frames.append(frame)
    return frames


def _install_asset_generators() -> None:
    if not (ASSET_DIR / "collie-happy-v2.png").exists():
        return

    idle_motion = [(0, 0, 1.0, 0), (0, -1, 1.01, 0), (0, 0, 1.0, 0), (0, 0, 0.995, 0)]
    walk_motion = [(-3, 1, 0.98, -1), (-1, -2, 1.0, 1), (1, 1, 0.99, -1), (3, -2, 1.0, 1)]
    working_motion = [
        (0, 1, 0.99, 0),
        (0, -2, 1.005, -1),
        (0, 0, 1.0, 0),
        (0, -1, 1.0, 1),
    ]
    concerned_motion = [
        (0, 0, 1.0, 0),
        (0, 1, 0.995, -1),
        (0, 0, 1.0, 0),
        (0, -1, 1.005, 1),
    ]
    happy_motion = [
        (0, 0, 1.0, 0),
        (0, -5, 1.025, -1),
        (0, 0, 1.0, 0),
        (0, -2, 1.01, 1),
    ]

    STATE_GENERATORS.update(
        {
            "idle": lambda: _asset_frames("happy-v2", idle_motion),
            "working": lambda: _asset_frames("play", working_motion),
            "walk": lambda: _asset_frames("walk", walk_motion),
            "walk_left": lambda: _asset_frames("walk", walk_motion, flip=True),
            "sleep": lambda: _asset_frames("sleep", [(0, 0, 1.0, 0), (0, 0, 1.012, 0)]),
            "happy": lambda: _asset_frames("happy-v2", happy_motion),
            "concerned": lambda: _asset_frames("chase", concerned_motion),
        }
    )


_install_asset_generators()


def generate_all_sprites() -> dict[str, list[Image.Image]]:
    """Generate all sprite frames for all animation states."""
    if Image is None:
        raise ImportError("Pillow is required. Install with: pip install Pillow")
    result = {}
    for state in RENDER_STATES:
        gen = STATE_GENERATORS.get(state)
        if gen:
            result[state] = gen()
    return result


def generate_spritesheet() -> Image.Image:
    """
    Generate a Codex-v2-style spritesheet: 8 columns x N rows.
    Each row is an animation state, each column is a frame.
    Layout matches Codex V2: 8 columns wide, 9+ rows of states.
    """
    all_sprites = generate_all_sprites()

    # Determine max frames per state (Codex uses up to 8)
    max_frames = max(len(frames) for frames in all_sprites.values())
    n_cols = min(max_frames, 8)
    n_rows = len(RENDER_STATES)

    sheet_w = n_cols * CELL_W
    sheet_h = n_rows * CELL_H

    sheet = Image.new("RGBA", (sheet_w, sheet_h), TRANSPARENT)

    for row_idx, state in enumerate(RENDER_STATES):
        frames = all_sprites.get(state, [])
        for col_idx, frame in enumerate(frames):
            if col_idx >= n_cols:
                break
            sheet.paste(frame, (col_idx * CELL_W, row_idx * CELL_H))

    return sheet
