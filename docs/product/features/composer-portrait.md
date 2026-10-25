# Composer portrait

**Status:** implemented, awaiting native platform acceptance

The composer keeps the illustrated Collie portrait, orange circle, layout,
and local bundled artwork. A shallow depth surface projects the head with
continuous yaw and pitch. The muzzle projects farther than the forehead, so
the snout turns relative to the eyes instead of shifting the whole portrait.
The mesh is 12 by 16 quads with a fixed chest root. It uses one frontal painting
from the original direction atlas, avoiding competing head silhouettes. The
turn range is deliberately bounded (about 29 degrees yaw and 15 degrees pitch);
this does not add unseen back-of-head artwork or full profile geometry.
Rotation feathers out above the chest instead of twisting the lower neck.
Independent, restrained ear sway and a small happy perk keep the ears alive.
The chest overshoots the circular clip so fur meets the lower ring edge.

Pointer attention covers the app window with a soft central dead zone. Leaving
the portrait ring does not change tracking. On Windows/macOS and Linux X11,
the visible app can continue tracking beyond its borders through a guarded,
read-only Electron bridge. Only content-relative pointer coordinates cross it;
there are no hooks, clicks/keys, screen capture, logs, or stored pointer history.
Polling is limited to about 15 Hz while outside the visible window and stops
when hidden. Electron cannot supply global pointer positions on Wayland;
there the portrait retains its last look until window pointer events resume.
See [Electron screen documentation](https://www.electronjs.org/docs/latest/api/screen/).
Windows native outside-window tracking has been exercised; macOS/X11 remain
native acceptance gates for this shared API path.
Engine waiting and error states retain priority;
work reduces attention and deep work adds aligned glasses.

A click raises the original illustrated foreleg, pivoted from the chest with
anticipation, a hold, and a gradual return. A double click promotes that reaction
to the happy reaction even during the first click's cooldown. Both clicks and
double clicks show the full tongue-out smile taken from the actual app logo.
Enter/Space activate the wave; Shift+Enter activates the smile. A small jaw
settle accompanies the painted mouth at its full proportions.
Original painted eyelids continue natural blinks. There is no automatic sleepy
or persistent closed-eye state on leave, blur, or inactivity.
Mouth and eyelid registration happens once at load; whole heads do not
crossfade. Current head position and the animation clock survive state changes.

The static portrait is visible until Canvas successfully draws, then hidden.
Texture failure or context loss restores it; context restoration restarts one
loop. Motion uses elapsed time with a 60 Hz draw ceiling, textures sized to the
visible portrait with DPR capped at two, and no React update per animation
frame. Hidden documents stop requesting animation frames. Reduced motion draws
one neutral frame. No inference, WebGL, WebGPU, or native platform renderer is
required. The same Canvas path supports Chromium's software rendering.

## Local review

From `collie-ui`, run:

```powershell
npm run build
node scripts/preview-portrait.cjs
```

This opens a separate preview with its own core/profile, without provider
credentials or real conversations. Its profile defaults to the OS temporary directory; set
`COLLIE_PORTRAIT_REVIEW_HOME` to choose another location. Do not use it to send
messages. Move the pointer across the empty chat and through the ring, click or
keyboard-activate the portrait, and leave/re-enter the window.

For real rendered regression checks, leave the preview open and run:

```powershell
$env:COLLIE_PORTRAIT_QA_OUTPUT = 'C:\path\outside-the-repository\portrait-evidence'
node scripts/verify-portrait-rig.cjs
```

The script uses the local debug port 9345 (`COLLIE_DEBUG_PORT` overrides it),
checks sixteen rendered directions and snout motion relative to the eyes,
window exit, single-silhouette rendering, open-mouth double click, painted
blink pixels, and reaction return. It records Canvas draw cost during unsent
typing.
It emulates focus for repeatability and restores that setting afterward.
It does not measure GPU presentation latency or establish native visual
acceptance. Evidence and runtime state must stay outside source control.

## Acceptance before release

Windows native inspection and Chromium pixel/time checks are useful evidence,
not proof for other platforms. Review the following on actual hardware:

- Windows at 100%, 125%, 150%, and 200% scaling; move between displays and test
  60/120/144 Hz. Inspect fur edges for mesh seams and the muzzle for stretching.
- macOS arm64 Retina and mixed-DPI external displays, focus/blur, and reduced
  motion. Intel Mac packaging remains outside this change.
- Linux X11 and Wayland with fractional scaling, accelerated and software
  rendering; inspect pointer leave and window focus behavior.
- Sleep/resume, minimize/restore, repeated mount/unmount, image load failure,
  Canvas unavailable, context loss/restoration, and reduced motion changed
  while the app is running. Hidden animation must stop and resume as one loop.
- Waiting, error, work, deep work, completion, pointer attention, rapid clicks,
  and keyboard activation. Check priorities and gradual return without a
  duplicate silhouette. Confirm that typing/scrolling remains responsive.

Unit/component tests cover muzzle rotation, triangle orientation, double-click promotion, geometry bounds, pointer scope, fallback ownership,
visibility lifecycle, reduced motion, load failure, and context recovery. Native
macOS/Linux acceptance and physical sleep/resume remain required before making
cross-platform smoothness claims.
