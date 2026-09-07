import dogPortraitSheet from '../assets/agents/dog-portrait-sheet-30.png'

const PORTRAIT_COLUMNS = 6
const PORTRAIT_ROWS = 5
const PORTRAIT_COUNT = PORTRAIT_COLUMNS * PORTRAIT_ROWS
// A small source gutter avoids sampling the neighboring tile at fractional DPI.
const CELL_PIXELS = 229
const GUTTER_PIXELS = 2
const VISIBLE_PIXELS = CELL_PIXELS - 2 * GUTTER_PIXELS

interface Props {
  name: string
  size?: number
}

function stablePortraitIndex(identity: string): number {
  let hash = 2166136261
  for (const character of identity) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return Math.abs(hash) % PORTRAIT_COUNT
}

export default function AgentAvatar({ name, size = 48 }: Props): React.JSX.Element {
  const index = stablePortraitIndex(name.normalize('NFKC').trim().toLowerCase())
  const column = index % PORTRAIT_COLUMNS
  const row = Math.floor(index / PORTRAIT_COLUMNS)

  return (
    <span
      className="agent-portrait"
      role="img"
      aria-label={`${name}'s dog portrait`}
      style={{
        width: size,
        height: size,
        backgroundImage: `url(${dogPortraitSheet})`,
        backgroundPosition: `${100 * (column * CELL_PIXELS + GUTTER_PIXELS) / (PORTRAIT_COLUMNS * CELL_PIXELS - VISIBLE_PIXELS)}% ${100 * (row * CELL_PIXELS + GUTTER_PIXELS) / (PORTRAIT_ROWS * CELL_PIXELS - VISIBLE_PIXELS)}%`,
        backgroundSize: `${100 * PORTRAIT_COLUMNS * CELL_PIXELS / VISIBLE_PIXELS}% ${100 * PORTRAIT_ROWS * CELL_PIXELS / VISIBLE_PIXELS}%`
      }}
    />
  )
}
