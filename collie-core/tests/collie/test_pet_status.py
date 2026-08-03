"""Desktop pet announcement lifecycle."""

from __future__ import annotations

import json

from collie_core.pet import collie_pet as pet_module
from collie_core.pet.collie_pet import ColliePet
from collie_core.pet.sprites import ANIM_STATES, RENDER_STATES


class _Root:
    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []
        self.cancelled: list[str] = []

    def after(self, delay: int, callback) -> str:
        self.scheduled.append((delay, callback))
        return "hide-job"

    def after_cancel(self, job: str) -> None:
        self.cancelled.append(job)


class _Label:
    def __init__(self) -> None:
        self.text = ""
        self.manager = ""
        self.forgotten = False

    def config(self, *, text: str) -> None:
        self.text = text

    def winfo_manager(self) -> str:
        return self.manager

    def pack(self, **_kwargs) -> None:
        self.manager = "pack"

    def pack_forget(self) -> None:
        self.manager = ""
        self.forgotten = True


def _pet() -> tuple[ColliePet, _Root, _Label, list[str]]:
    pet = ColliePet.__new__(ColliePet)
    root = _Root()
    label = _Label()
    states: list[str] = []
    pet.root = root
    pet._status_label = label
    pet._status_hide_job = None
    pet._label = object()
    pet.set_state = states.append
    return pet, root, label, states


def test_progress_announcement_is_transient() -> None:
    pet, root, label, states = _pet()

    pet._set_status("  Working   on your route  ")

    assert label.text == "Working on your route"
    assert label.manager == "pack"
    assert states == ["working"]
    assert root.scheduled[0][0] == 12_000
    assert pet._status_hide_job == "hide-job"


def test_completion_stays_until_acknowledged() -> None:
    pet, root, label, states = _pet()

    pet._set_status("Finished. Your result is ready.")

    assert label.manager == "pack"
    # v2 pet feature (d14110e) renamed the completion state to "completion".
    assert states == ["completion"]
    assert root.scheduled == []

    pet._set_status("dismiss")

    assert label.forgotten is True
    assert pet._status_hide_job is None


def test_explicit_status_state_keeps_bubble_and_animation_in_sync() -> None:
    pet, root, label, states = _pet()

    pet._set_status("Looking through reliable sources…", state="walk")

    assert label.text == "Looking through reliable sources…"
    assert states == ["walk"]
    assert root.scheduled[0][0] == 12_000


def test_approval_announcement_stays_until_acknowledged() -> None:
    pet, root, label, states = _pet()

    pet._set_status("Approval needed in Collie.", state="concerned")

    assert label.manager == "pack"
    assert states == ["concerned"]
    assert root.scheduled == []


def test_long_status_is_shortened_at_a_word_boundary() -> None:
    text = (
        "Working through a deliberately long description that should fit comfortably "
        "inside the desktop speech bubble without clipping any words"
    )

    rendered = ColliePet._format_status_text(text)

    assert len(rendered) <= 78
    assert rendered.endswith("…")
    assert "clipp…" not in rendered


def test_pet_exposes_only_six_meaningful_animations() -> None:
    assert ANIM_STATES == [
        "idle",
        "working",
        "walk",
        "sleep",
        "happy",
        "concerned",
    ]
    assert RENDER_STATES == [*ANIM_STATES, "walk_left"]


def test_non_windows_work_area_uses_screen_bounds(monkeypatch) -> None:
    class _Screen:
        @staticmethod
        def winfo_screenwidth() -> int:
            return 1920

        @staticmethod
        def winfo_screenheight() -> int:
            return 1080

    monkeypatch.setattr(pet_module.sys, "platform", "linux")

    assert pet_module._screen_work_area(_Screen()) == (0, 0, 1920, 1080)


class _FakeRoot:
    """Minimal Tk root double: records wm_attributes calls, optionally
    rejecting -transparentcolor exactly like Linux/X11 tkinter does."""

    def __init__(self, reject_transparentcolor: bool) -> None:
        self.reject_transparentcolor = reject_transparentcolor
        self.attribute_calls: list[str] = []
        self.bg: str | None = None

    def title(self, _title: str) -> None:
        pass

    def overrideredirect(self, _value: bool) -> None:
        pass

    def wm_attributes(self, *args) -> None:
        self.attribute_calls.append(args[0])
        if args[0] == "-transparentcolor" and self.reject_transparentcolor:
            raise pet_module.tk.TclError(
                'bad attribute "-transparentcolor": must be -alpha, '
                "-fullscreen, -topmost, -type, or -zoomed"
            )

    def config(self, **kwargs) -> None:
        self.bg = kwargs.get("bg")


class _FakeLabel:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def pack(self, *_args, **_kwargs) -> None:
        pass

    def bind(self, *_args, **_kwargs) -> None:
        pass


def test_build_window_survives_missing_transparentcolor_support(monkeypatch) -> None:
    """Linux/X11 tkinter rejects -transparentcolor; the pet must still open.

    Regression: the unguarded call crashed the whole pet process on non-Windows
    (exit 1 -> Electron's respawn cap -> no pet ever appears).
    """
    monkeypatch.setattr(pet_module.tk, "Label", _FakeLabel)
    root = _FakeRoot(reject_transparentcolor=True)

    pet = ColliePet.__new__(ColliePet)
    pet.root = root
    pet._build_window()

    assert "-transparentcolor" in root.attribute_calls
    assert root.bg == "#010203"


def test_build_window_applies_transparentcolor_when_supported(monkeypatch) -> None:
    """Windows accepts the attribute, so the chroma key must still be set."""
    monkeypatch.setattr(pet_module.tk, "Label", _FakeLabel)
    root = _FakeRoot(reject_transparentcolor=False)

    pet = ColliePet.__new__(ColliePet)
    pet.root = root
    pet._build_window()

    assert "-transparentcolor" in root.attribute_calls
    assert root.bg == "#010203"


def test_saving_position_preserves_enabled_preference(tmp_path, monkeypatch) -> None:
    settings_file = tmp_path / "pet_settings.json"
    settings_file.write_text(
        json.dumps({"enabled": False, "scale": 1.5}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pet_module, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(pet_module, "SETTINGS_FILE", settings_file)

    pet_module.save_position(120, 240)

    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        "enabled": False,
        "scale": 1.5,
        "x": 120,
        "y": 240,
    }
