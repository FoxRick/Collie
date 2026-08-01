import BrandLogo from '../BrandLogo'

export default function ConnectorIcon({
  providerId,
  name,
  size = 42
}: {
  providerId: string
  name: string
  size?: number
}): React.JSX.Element {
  return <BrandLogo brand={providerId} name={name} size={size} />
}
