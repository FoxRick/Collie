import dogPortraitSheet from '../assets/agents/dog-portrait-sheet-20.png'

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
  return Math.abs(hash) % 20
}

export default function AgentAvatar({ identity, name, size = 48 }: Props): React.JSX.Element {
  const index = stablePortraitIndex(identity)
  const column = index % 5
  const row = Math.floor(index / 5)
  // The generated sheet cells are 2:1. Keep that source aspect ratio and
  // center-crop each cell into the square avatar instead of squeezing it.
  const horizontalPosition = ((column * 2 + 0.5) / 9) * 100

  return (
    <span
      className="agent-portrait"
      role="img"
      aria-label={`${name}'s dog portrait`}
      style={{
        width: size,
        height: size,
        backgroundImage: `url(${dogPortraitSheet})`,
        backgroundPosition: `${horizontalPosition}% ${row * (100 / 3)}%`,
        backgroundSize: '1000% 400%'
      }}
    />
  )
}
