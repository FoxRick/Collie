"""Contract tests for the preserved full-body Collie v2 desktop pet."""

from collie_core.ipc.thinking import PET_V2_STATE_BY_THINKING_STATE, PHRASES
from collie_core.pet.v2 import (
    DEEP_WORK_THRESHOLD_MS,
    FRAME_SEQUENCES,
    V2AnimationController,
    V2SpriteRenderer,
    asset_paths,
    quantize_pointer_direction,
)


def test_pet_state_map_covers_all_phrases_and_stays_in_command_allowlist() -> None:
    # Every thinking phrase the core can emit maps to a desktop-pet v2 state,
    # and that state is one the Electron command allowlist accepts verbatim.
    allowed = {"idle", "working", "review", "error", "completion"}
    assert set(PET_V2_STATE_BY_THINKING_STATE) == set(PHRASES)
    assert set(PET_V2_STATE_BY_THINKING_STATE.values()) <= allowed
    # Consistency with the chat portrait: summarizing is visible work, not
    # the review/sniff pose (the portrait maps it to "working" as well).
    assert PET_V2_STATE_BY_THINKING_STATE["summarizing"] == "working"


def test_preserved_assets_and_frame_contract() -> None:
    assert set(asset_paths()) == {"atlas", "glasses", "bone"}
    renderer = V2SpriteRenderer()
    assert len(renderer.frames_for("pointer_look")) == 16
    assert FRAME_SEQUENCES["walk_right"].cells == tuple(range(8, 16))
    assert FRAME_SEQUENCES["walk_left"].cells == tuple(range(16, 24))


def test_glasses_require_uninterrupted_work_and_clear_on_exit() -> None:
    controller = V2AnimationController("working", 100)
    assert controller.snapshot(100 + DEEP_WORK_THRESHOLD_MS - 1).state == "working"
    assert controller.snapshot(100 + DEEP_WORK_THRESHOLD_MS).state == "deep_work_glasses"
    assert all(
        sequence.asset != "glasses"
        for state, sequence in FRAME_SEQUENCES.items()
        if state != "deep_work_glasses"
    )
    controller.set_base_state("review", 100 + DEEP_WORK_THRESHOLD_MS + 1)
    assert controller.snapshot(100 + DEEP_WORK_THRESHOLD_MS + 1).state == "review"


def test_directional_walk_and_pointer_states_are_bounded() -> None:
    controller = V2AnimationController("idle", 0)
    assert controller.start_directional_motion("walk_right", 1)
    assert controller.snapshot(1).state == "walk_right"
    controller.set_base_state("idle", 2)
    assert controller.snapshot(2).state == "idle"

    controller.set_pointer_target(4, 3)
    assert controller.snapshot(3).state == "pointer_look"
    controller.set_pointer_target(None, 4)
    assert controller.snapshot(4).state == "idle"

    assert quantize_pointer_direction(0, -30) == 0
    assert quantize_pointer_direction(30, 0) == 4
