const COLLIE_ICON = new URL('../../../../build/icon.png', import.meta.url).href

interface Props {
  size?: number
  className?: string
  title?: string
}

export default function CollieFace({
  size = 24,
  className,
  title = 'Collie'
}: Props): React.JSX.Element {
  return (
    <img
      src={COLLIE_ICON}
      width={size}
      height={size}
      alt={title}
      className={className}
      draggable={false}
    />
  )
}
