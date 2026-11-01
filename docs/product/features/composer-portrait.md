# Composer portrait

**Status:** implemented locally; awaiting release acceptance

Collei stays in one fixed painted pose beside the composer. The artwork comes
from the same `portrait/happy.webp` used in the website features tile. The
head does not follow the pointer or rotate. Facial animation uses the mouth,
eyelids, ears and original raised foreleg, with one visible Canvas 2D surface.

## Expressions and idle activity

The portrait responds to typing, thinking/planning, work, research/checking,
helper handoffs, waiting for helpers, writing, input/approval requests,
completion and errors. Blocking input/error states take priority over short
reactions. Click or Enter/Space gives a happy paw wave; double-click or
Shift+Enter promotes the happy reaction. Three minutes genuinely idle enters
sleep; typing, work and active helpers keep Collei awake.

The painted head is approximately 12% smaller than the earlier expression
preview, with a fixed optical shift right and additional ear headroom. Only
the lower chest stretches to meet the circle. Expression changes retain the
current channel values and animation clock; position and scale stay fixed.

While awake, blinks occur roughly every 3–4 seconds with small timing variation
and an occasional double blink. Ear perks/relaxations occur between quiet
holds, approximately every three seconds. These movements do not require
clicks or pointer activity. Reduced motion disables automatic movement and
shows a still for the current expression.

## Assets and lifecycle

The offline generator derives 49 mouth, 33 eyelid and 33 ear frames from the
existing paintings. It also extracts the foreleg and makes a matching static
fallback. Source hashes and decoded sizes are recorded in
`portrait/expressions-v2.json`. There are no uploads, generation services,
runtime inference, WebGL or WebGPU. Atlas decoding is about 16.8 MiB before
browser caching and graphics-driver overhead.

The renderer draws one completed mouth frame, one registered eye patch, an
ear strip and the foreleg when needed. It never crossfades entire heads.
Adjacent drawing regions overlap within identical fur to avoid thin seams at
fractional DPI. The fallback is hidden only after a successful canvas draw;
image/context failures restore it. No new canvases or textures are allocated
on state changes. One elapsed-time loop is capped at 60 Hz with DPR capped at
two. Hidden documents stop rendering; unmount releases the backing store and
listeners. The desktop-pointer IPC path is no longer exposed.

## Local verification

From `collie-ui/`, run the focused portrait tests, typecheck and build.
`scripts/generate-expression-assets.cjs` regenerates local artwork using
Electron. `scripts/preview-expressions.cjs` renders the standalone lab using
the production painter. `scripts/preview-portrait.cjs` opens the built app in
an isolated core/profile. `scripts/verify-expressions.cjs` checks the app's
expressions, context recovery, reduced motion, fractional DPR and a 90-second
state-switch soak. Do not use this verifier against real user profiles.

Expression controls require both `collie.ui-ux-preview` and
`collie.expression-preview` sessionStorage flags and affect only the local
portrait. Evidence stays outside the source repository. The soak uses focus
emulation for consistent visibility; it is restored afterward.

A short Windows soak does not prove long-term leak freedom. Native process
working sets are not dedicated VRAM; GPU counters may be unavailable. Native
mixed-DPI displays, sleep/resume, graphics-driver recovery and macOS/Linux
acceptance remain release gates.
