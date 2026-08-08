import dogPortraitSheet from '../assets/agents/dog-portrait-sheet-30.png'

const PORTRAIT_COLUMNS = 6
const PORTRAIT_ROWS = 5
const PORTRAIT_COUNT = PORTRAIT_COLUMNS * PORTRAIT_ROWS

interface Props {
  identity: string
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

export default function AgentAvatar({ identity, name, size = 48 }: Props): React.JSX.Element {
  const index = stablePortraitIndex(identity)
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
        backgroundPosition: `${column * (100 / (PORTRAIT_COLUMNS - 1))}% ${row * (100 / (PORTRAIT_ROWS - 1))}%`,
        backgroundSize: `${PORTRAIT_COLUMNS * 100}% ${PORTRAIT_ROWS * 100}%`
      }}
    />
  )
}
