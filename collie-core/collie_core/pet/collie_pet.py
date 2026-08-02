"""
Floating Border Collie desktop pet window.

Features:
- Always-on-top, frameless transparent window
- Draggable by click-and-drag anywhere on the pet
- Six focused sprite states tied to Collie's real activity
- Random personality transitions
- Right-click context menu
- Scale support (wheel scroll)
- Position remembered between sessions
- Inspired by the Codex V2 pet system (spritesheet frames, state row layout)
  and the Hermes desktop FloatingPet component.
"""

from __future__ import annotations

import json
import random
import sys
import tkinter as tk
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

from .sprites import (
    ANIM_STATES,
    CELL_H,
    CELL_W,
    FRAME_DURATIONS,
    RENDER_STATES,
    STATE_GENERATORS,
)


# Persistence — honor COLLIE_HOME like the rest of the runtime (the Electron
# shell writes commands and settings to the same directory).
def _collie_dir() -> Path:
    import os

    root = os.environ.get("COLLIE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".collie"


SETTINGS_DIR = _collie_dir()
SETTINGS_FILE = SETTINGS_DIR / "pet_settings.json"
COMMAND_FILE = SETTINGS_DIR / "pet_command.json"

# How often the pet sniffs the command file for shell requests (F078)
COMMAND_POLL_MS = 500

# Default scale relative to sprite cell size
DEFAULT_SCALE = 1.0
MIN_SCALE = 0.25
MAX_SCALE = 3.0
SCALE_STEP = 0.05

# Idle personality: seconds between random state transitions
PERSONALITY_MIN_IDLE_S = 45
PERSONALITY_MAX_IDLE_S = 90

# Pet "moods" / autonomous states the pet can wander into
AUTONOMOUS_STATES = ["idle", "sleep"]
WALK_STATES = ["walk", "walk_left"]

LEGACY_STATE_ALIASES = {
    "sit": "working",
    "jump": "happy",
    "wag": "happy",
    "run": "walk",
    "run_right": "walk",
    "chase": "concerned",
    "play": "working",
    "belly": "idle",
    "walk_right": "walk",
}

STATUS_MAX_CHARS = 78
STATUS_VISIBLE_MS = 12_000

# Safe border margin from screen edges
EDGE_MARGIN = 16


def _screen_work_area(root: tk.Misc) -> Tuple[int, int, int, int]:
    """Return the usable primary-screen bounds, excluding the Windows taskbar."""
    fallback = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        spi_getworkarea = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(
            spi_getworkarea, 0, ctypes.byref(rect), 0
        ):
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return fallback


def _ensure_settings_dir() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def load_position() -> Tuple[int, int]:
    """Load saved pet position or default to bottom-right."""
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            x = data.get("x")
            y = data.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return int(x), int(y)
    except (json.JSONDecodeError, OSError):
        pass
    return -1, -1  # "unset" — caller computes default


def save_position(x: int, y: int) -> None:
    _ensure_settings_dir()
    data = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    data.update({"x": x, "y": y})
    SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")


def load_scale() -> float:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            s = data.get("scale", DEFAULT_SCALE)
            if isinstance(s, (int, float)):
                return max(MIN_SCALE, min(MAX_SCALE, float(s)))
    except (json.JSONDecodeError, OSError):
        pass
    return DEFAULT_SCALE


def save_scale(scale: float) -> None:
    _ensure_settings_dir()
    data = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    data["scale"] = scale
    SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")


class ColliePet:
    """Floating desktop Border Collie pet."""

    def __init__(self) -> None:
        if Image is None or ImageTk is None:
            raise ImportError(
                "Pillow is required. Install with: pip install Pillow"
            )

        self.root = tk.Tk()
        self.root.withdraw()  # Hide until ready

        # State
        self._state = "idle"
        self._frame_index = 0
        self._animating = False
        self._anim_job: Optional[str] = None
        self._scale = load_scale()
        self._facing_right = False
        self._dragging = False
        self._drag_offset = (0, 0)
        self._status_hide_job: Optional[str] = None

        # Generated sprite frames: state -> list of PhotoImage (scaled)
        self._frames: Dict[str, List[ImageTk.PhotoImage]] = {}
        self._generate_all_frames()

        # Build window
        self._build_window()

        # Position
        x, y = load_position()
        if x < 0 and y < 0:
            # Default: bottom-right of the usable desktop, above the taskbar.
            self.root.update_idletasks()
            left, top, right, bottom = _screen_work_area(self.root)
            pet_w = max(int(CELL_W * self._scale), self.root.winfo_reqwidth())
            pet_h = max(int(CELL_H * self._scale), self.root.winfo_reqheight())
            x = max(left + EDGE_MARGIN, right - pet_w - EDGE_MARGIN)
            y = max(top + EDGE_MARGIN, bottom - pet_h - EDGE_MARGIN)
        self._set_position(x, y)

        # Start idle animation
        self.set_state("idle")

        # Show window
        self.root.deiconify()

        # Personality timer — occasional autonomous state changes
        self._schedule_personality()

        # Command file poller — shell buttons (Wave/Sit/Jump/Sleep) write here
        self._last_command_ts: float = 0.0
        self._roaming = False
        self._poll_command_file()

        # Bind right-click menu
        self._build_context_menu()

        # Center pet widget handler (double-click / middle click centers)
        self.root.bind("<Double-Button-1>", self._on_center)
        self.root.bind("<Button-2>", self._on_center)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        """Create the transparent, frameless, always-on-top window."""
        self.root.title("Collie Pet")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)

        # Transparency via color key. A near-black key prevents the bright
        # magenta fringe Windows otherwise shows around alpha-smoothed sprites.
        self._chroma_key = "#010203"
        self.root.config(bg=self._chroma_key)
        self.root.wm_attributes("-transparentcolor", self._chroma_key)

        # Try to keep off the taskbar (Windows)
        try:
            self.root.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self._status_label = tk.Label(
            self.root,
            text="",
            wraplength=260,
            justify="center",
            bg="#fff3d6",
            fg="#1f241f",
            padx=14,
            pady=9,
            bd=2,
            relief="solid",
            font=("Segoe UI", 11),
            cursor="hand2",
        )
        self._status_label.bind("<Button-1>", self._dismiss_status)

        # Label that holds the sprite
        self._label = tk.Label(
            self.root,
            bg=self._chroma_key,
            bd=0,
            highlightthickness=0,
            cursor="fleur",
        )
        self._label.pack()

        # Drag bindings on the label (so transparent areas don't grab)
        self._label.bind("<ButtonPress-1>", self._on_drag_start)
        self._label.bind("<B1-Motion>", self._on_drag_move)
        self._label.bind("<ButtonRelease-1>", self._on_drag_end)

        # Scale on mouse wheel
        self._label.bind("<MouseWheel>", self._on_scale)
        # Linux scroll
        self._label.bind("<Button-4>", lambda e: self._on_scale(e, 1))
        self._label.bind("<Button-5>", lambda e: self._on_scale(e, -1))

    def _build_context_menu(self) -> None:
        """Right-click context menu."""
        menu = tk.Menu(self.root, tearoff=0)

        state_menu = tk.Menu(menu, tearoff=0)
        for state in ANIM_STATES:
            state_menu.add_command(
                label=state.title(),
                command=lambda s=state: self.set_state(s),
            )
        menu.add_cascade(label="Animation State", menu=state_menu)

        menu.add_separator()

        # Scale submenu
        scale_menu = tk.Menu(menu, tearoff=0)
        for pct in [25, 50, 75, 100, 125, 150, 200, 250, 300]:
            scale_menu.add_command(
                label=f"{pct}%",
                command=lambda s=pct / 100.0: self._set_scale(s),
            )
        menu.add_cascade(label="Scale", menu=scale_menu)

        menu.add_separator()
        menu.add_command(label="Reset Position", command=self._on_center)
        menu.add_command(label="Quit", command=self.destroy)

        def show_menu(event: tk.Event) -> None:
            menu.tk_popup(event.x_root, event.y_root)

        self.root.bind("<Button-3>", show_menu)
        self._context_menu = menu

    # ------------------------------------------------------------------
    # Sprite frame generation (lazy, scaled)
    # ------------------------------------------------------------------

    def _generate_all_frames(self) -> None:
        """Pre-render all animation state frames at the current scale."""
        self._frames.clear()
        for state in RENDER_STATES:
            gen = STATE_GENERATORS.get(state)
            if gen is None:
                continue
            pil_frames = gen()
            scaled: List[ImageTk.PhotoImage] = []
            for pil_img in pil_frames:
                if self._scale != 1.0:
                    w = max(1, int(CELL_W * self._scale))
                    h = max(1, int(CELL_H * self._scale))
                    pil_img = pil_img.resize((w, h), Image.LANCZOS)
                scaled.append(ImageTk.PhotoImage(pil_img))
            self._frames[state] = scaled

    def _regenerate_frames(self) -> None:
        """Regenerate frames after a scale change."""
        was_state = self._state
        was_frame = self._frame_index
        self._stop_animation()
        self._generate_all_frames()
        if was_state in self._frames:
            frames = self._frames[was_state]
            idx = min(was_frame, len(frames) - 1)
            self._label.config(image=frames[idx])
        self._start_animation()
        # Re-clamp position since size changed
        self._reclamp_position()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        """Transition to a new animation state."""
        if state not in self._frames or not self._frames[state]:
            return
        self._stop_animation()
        self._state = state
        self._frame_index = 0

        # Update facing for walk directions
        if state == "walk":
            self._facing_right = True
        elif state == "walk_left":
            self._facing_right = False

        self._label.config(image=self._frames[state][0])
        self._start_animation()

    def _start_animation(self) -> None:
        if self._animating:
            return
        self._animating = True
        self._advance_frame()

    def _stop_animation(self) -> None:
        self._animating = False
        if self._anim_job is not None:
            self.root.after_cancel(self._anim_job)
            self._anim_job = None

    def _advance_frame(self) -> None:
        if not self._animating:
            return
        frames = self._frames.get(self._state, [])
        if not frames:
            self._animating = False
            return

        durations = FRAME_DURATIONS.get(self._state, [150] * len(frames))
        self._frame_index = (self._frame_index + 1) % len(frames)
        delay = (
            durations[self._frame_index]
            if self._frame_index < len(durations)
            else 150
        )
        self._label.config(image=frames[self._frame_index])

        self._anim_job = self.root.after(delay, self._advance_frame)

    # ------------------------------------------------------------------
    # Personality — autonomous state transitions
    # ------------------------------------------------------------------

    def _schedule_personality(self) -> None:
        """Schedule a random personality-driven state change."""
        delay = random.randint(PERSONALITY_MIN_IDLE_S, PERSONALITY_MAX_IDLE_S) * 1000

        def tick() -> None:
            if not self.root.winfo_exists():
                return
            # Only change from non-animated/idle-ish states
            current = self._state
            if current in AUTONOMOUS_STATES:
                # Roaming is opt-in; the default pet stays focused near its work.
                if random.random() < 0.1 and getattr(self, "_roaming", False):
                    self._walk_across()
                else:
                    new_state = random.choice(AUTONOMOUS_STATES)
                    if new_state != current:
                        self.set_state(new_state)
            elif current in WALK_STATES:
                # Finish walk, transition to idle
                self.set_state("idle")
            self._schedule_personality()

        self.root.after(delay, tick)

    # ------------------------------------------------------------------
    # Shell commands — Pet controls widget writes ~/.collie/pet_command.json
    # ------------------------------------------------------------------

    def _poll_command_file(self) -> None:
        """Apply commands from the Electron shell (F078)."""
        try:
            if COMMAND_FILE.exists():
                data = json.loads(COMMAND_FILE.read_text(encoding="utf-8"))
                ts = float(data.get("ts") or 0)
                if ts > self._last_command_ts:
                    if self._last_command_ts > 0:
                        self._apply_command(str(data.get("command") or ""))
                    self._last_command_ts = ts
        except (json.JSONDecodeError, OSError, ValueError, tk.TclError):
            pass
        if self.root.winfo_exists():
            self.root.after(COMMAND_POLL_MS, self._poll_command_file)

    def _apply_command(self, command: str) -> None:
        command = command.strip()
        if command.startswith("status:"):
            payload = command.split(":", 1)[1]
            state, separator, text = payload.partition("|")
            if separator and state in ANIM_STATES:
                self._set_status(text, state=state)
            else:
                self._set_status(payload)
            return
        command = command.lower()
        command = LEGACY_STATE_ALIASES.get(command, command)
        if command in self._frames:
            self.set_state(command)
        elif command == "wave":
            self.set_state("happy")
        elif command in ("working", "walk", "sleep", "concerned"):
            self.set_state(command)
        elif command == "hide":
            self.root.withdraw()
        elif command == "show":
            self.root.deiconify()
        elif command == "roam":
            self._roaming = True
        elif command == "stay":
            self._roaming = False
            if self._state in WALK_STATES:
                self.set_state("idle")
        elif command.startswith("size:"):
            try:
                self._set_scale(float(command.split(":", 1)[1]))
            except (ValueError, IndexError):
                pass
        elif command == "quit":
            self.destroy()

    def _set_status(self, text: str, *, state: Optional[str] = None) -> None:
        """Show a concise work update above the pet."""
        clean = self._format_status_text(text)
        if not clean:
            return
        if clean.lower() in {"dismiss", "clear", "hide"}:
            self._dismiss_status()
            return

        if self._status_hide_job is not None:
            self.root.after_cancel(self._status_hide_job)
            self._status_hide_job = None

        self._status_label.config(text=clean)
        if not self._status_label.winfo_manager():
            self._status_label.pack(padx=6, pady=(4, 0), before=self._label)
        try:
            self.root.update_idletasks()
            self._reclamp_position()
        except (AttributeError, tk.TclError):
            # Lightweight test doubles and a closing Tk window may not expose
            # geometry methods. The status itself can still be updated.
            pass

        lowered = clean.lower()
        is_completion = state == "happy" or lowered.startswith(("finished", "done", "all done"))
        needs_attention = lowered.startswith(("approval needed", "waiting for approval"))
        if state in ANIM_STATES:
            self.set_state(state)
        elif lowered.startswith(("working", "thinking", "writing", "sorting", "mapping")):
            self.set_state("working")
        elif lowered.startswith(("checking", "fetching", "looking", "sniffing")):
            self.set_state("walk")
        elif lowered.startswith(("i hit a snag", "uh oh", "error", "couldn't", "could not")):
            self.set_state("concerned")
        elif is_completion:
            self.set_state("happy")

        # Progress notes are transient. Completion announcements remain until
        # the user clicks the bubble or returns to the Collie window.
        if not is_completion and not needs_attention:
            self._status_hide_job = self.root.after(STATUS_VISIBLE_MS, self._dismiss_status)

    @staticmethod
    def _format_status_text(text: str) -> str:
        """Normalize and shorten bubble text without cutting through a word."""
        clean = " ".join(text.split())
        if len(clean) <= STATUS_MAX_CHARS:
            return clean
        shortened = clean[: STATUS_MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(".,;:!?")
        return f"{shortened}…"

    def _dismiss_status(self, _event: Optional[tk.Event] = None) -> None:
        """Acknowledge and hide the current desktop announcement."""
        if self._status_hide_job is not None:
            self.root.after_cancel(self._status_hide_job)
            self._status_hide_job = None
        self._status_label.pack_forget()

    def _walk_across(self) -> None:
        """Perform an autonomous walk across part of the screen."""
        screen_w = self.root.winfo_screenwidth()
        x = self.root.winfo_x()
        pet_w = int(CELL_W * self._scale)

        # Walk toward center if at edge, otherwise random direction
        if x < screen_w * 0.2:
            direction = "walk"
        elif x > screen_w * 0.8:
            direction = "walk_left"
        else:
            direction = random.choice(WALK_STATES)

        self.set_state(direction)
        step = 6 if direction == "walk" else -6
        steps = random.randint(8, 20)

        def move(remaining: int) -> None:
            if remaining <= 0 or not self.root.winfo_exists():
                if self.root.winfo_exists():
                    self.set_state("idle")
                return
            cur_x = self.root.winfo_x()
            cur_y = self.root.winfo_y()
            new_x = max(EDGE_MARGIN, min(screen_w - pet_w - EDGE_MARGIN, cur_x + step))
            self._set_position(new_x, cur_y)
            self.root.after(80, lambda: move(remaining - 1))

        self.root.after(200, lambda: move(steps))

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _on_drag_start(self, event: tk.Event) -> None:
        self._dragging = True
        self._drag_offset = (event.x, event.y)
        self._label.config(cursor="fleur")
        # Pause walk animation during drag
        if self._state in WALK_STATES:
            self.set_state("idle")

    def _on_drag_move(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        x = self.root.winfo_x() + event.x - self._drag_offset[0]
        y = self.root.winfo_y() + event.y - self._drag_offset[1]
        self._set_position(x, y)

    def _on_drag_end(self, event: tk.Event) -> None:
        self._dragging = False
        save_position(self.root.winfo_x(), self.root.winfo_y())

    # ------------------------------------------------------------------
    # Scale (mouse wheel)
    # ------------------------------------------------------------------

    def _on_scale(self, event: tk.Event, direction: int = 0) -> None:
        if direction == 0:
            # Windows: event.delta is ±120 per notch
            direction = 1 if event.delta > 0 else -1
        new_scale = self._scale + direction * SCALE_STEP
        self._set_scale(new_scale)

    def _set_scale(self, scale: float) -> None:
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        if abs(scale - self._scale) < 0.001:
            return
        self._scale = scale
        save_scale(scale)
        self._regenerate_frames()

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def _set_position(self, x: int, y: int) -> None:
        """Move window to x, y without saving."""
        self.root.geometry(f"+{x}+{y}")

    def _reclamp_position(self) -> None:
        """Ensure the window stays fully on-screen after resize."""
        self.root.update_idletasks()
        left, top, right, bottom = _screen_work_area(self.root)
        window_w = max(int(CELL_W * self._scale), self.root.winfo_reqwidth())
        window_h = max(int(CELL_H * self._scale), self.root.winfo_reqheight())
        x = max(
            left + EDGE_MARGIN,
            min(right - window_w - EDGE_MARGIN, self.root.winfo_x()),
        )
        y = max(
            top + EDGE_MARGIN,
            min(bottom - window_h - EDGE_MARGIN, self.root.winfo_y()),
        )
        self._set_position(x, y)

    def _on_center(self, event: tk.Event = None) -> None:
        """Center the pet on screen."""
        left, top, right, bottom = _screen_work_area(self.root)
        pet_w = int(CELL_W * self._scale)
        pet_h = int(CELL_H * self._scale)
        x = left + (right - left - pet_w) // 2
        y = top + (bottom - top - pet_h) // 2
        self._set_position(x, y)
        save_position(x, y)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the Tk event loop."""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.destroy()

    def destroy(self) -> None:
        """Save position and clean up."""
        try:
            if self.root.winfo_exists():
                save_position(self.root.winfo_x(), self.root.winfo_y())
            self._stop_animation()
            self.root.destroy()
        except (tk.TclError, RuntimeError):
            pass

    # ------------------------------------------------------------------
    # API for external control (e.g. from the harness)
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def scale(self) -> float:
        return self._scale

    def wave(self) -> None:
        """Public: make the pet do a happy/wag animation, then return to idle."""
        self.set_state("happy")
        # Return to idle after one loop
        frames = self._frames.get("happy", [])
        durations = FRAME_DURATIONS.get("happy", [])
        total_ms = sum(durations[: len(frames)]) if frames else 2000

        def back_to_idle() -> None:
            if self.root.winfo_exists() and self._state == "happy":
                self.set_state("idle")

        self.root.after(total_ms, back_to_idle)

    def sit(self) -> None:
        self.set_state("working")

    def jump(self) -> None:
        """Legacy API: use the single happy celebration animation."""
        self.set_state("happy")
        frames = self._frames.get("happy", [])
        durations = FRAME_DURATIONS.get("happy", [])
        total_ms = sum(durations[: len(frames)]) if frames else 2000
        self.root.after(total_ms, lambda: (
            self.set_state("idle")
            if self.root.winfo_exists() and self._state == "happy"
            else None
        ))

    def toggle_sleep(self) -> None:
        if self._state == "sleep":
            self.set_state("idle")
        else:
            self.set_state("sleep")
