# Composer portrait

**Status:** implemented, awaiting native platform acceptance

The composer keeps the illustrated Collie portrait, orange circle, layout,
and local bundled artwork. Its renderer uses one portrait texture on a small
Canvas 2D mesh (12 by 16 quads) with a fixed chest root. Bounded head, eye-region,
ear, and chest deformation gives continuous attention without blending whole
heads. This is a modest illustration rig, not a three-dimensional head turn;
the original face angle remains recognizable at every target.

Pointer attention covers the app window with a soft central dead zone. Leaving
the portrait ring does not change tracking. Leaving the window or losing focus
returns smoothly to center. Engine waiting and error states retain priority;
work reduces attention and deep work adds aligned glasses. Click and completion
reactions use the existing paw with anticipation, rise, hold, and return. The
head's current position and animation clock survive state changes.

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

This opens a separate preview with its own core/profile, without provider credentials or real
conversations. Its profile defaults to the OS temporary directory; set
`COLLIE_PORTRAIT_REVIEW_HOME` to choose another location. Do not use it to send
messages. Move the pointer across the empty chat and through the ring, click or
keyboard-activate the portrait, and leave/re-enter the window.

For real rendered regression checks, leave the preview open and run:

```powershell
$env:COLLIE_PORTRAIT_QA_OUTPUT = 'C:\path\outside-the-repository\portrait-evidence'
node scripts/verify-portrait-rig.cjs
```

The script uses the local debug port 9345 (`COLLIE_DEBUG_PORT` overrides it),
checks all eight rendered directions, window exit, single-silhouette rendering,
and reaction return, and records Canvas draw cost while typing unsent text.
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

Unit/component tests cover geometry bounds, pointer scope, fallback ownership,
visibility lifecycle, reduced motion, load failure, and context recovery. Native
macOS/Linux acceptance and physical sleep/resume remain required before making
cross-platform smoothness claims.

