interface Props {
  data: Record<string, unknown>
}

export default function WeatherCard({ data }: Props): React.JSX.Element {
  const current = data.current as Record<string, unknown> | undefined
  const forecast = data.forecast as Array<Record<string, unknown>> | undefined
  const location = (data.location as string) || ''

  return (
    <div className="collie-reveal mt-3 w-full max-w-md overflow-hidden rounded-2xl border"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      {current && (
        <div className="flex items-center gap-4 p-4">
          <span className="text-4xl">{current.icon as string}</span>
          <div>
            <div className="text-2xl font-bold" style={{ color: 'var(--collie-nose)' }}>
              {current.temp as string}°C
            </div>
            <div style={{ color: 'var(--collie-paw)' }}>
              {current.condition as string} — {location}
            </div>
            <div className="mt-1 flex gap-3 text-xs" style={{ color: 'var(--collie-paw)' }}>
              <span>Feels like {current.feels_like as string}°C</span>
              <span>Humidity {current.humidity as string}%</span>
              <span>Wind {current.wind_speed as string} km/h</span>
            </div>
          </div>
        </div>
      )}
      {forecast && forecast.length > 0 && (
        <div className="flex border-t" style={{ borderColor: 'var(--collie-fur)' }}>
          {forecast.slice(1, 6).map((day, i) => (
            <div key={i} className="flex-1 p-3 text-center">
              <div className="text-xs font-semibold" style={{ color: 'var(--collie-paw)' }}>
                {(day.date as string)?.slice(5) || ''}
              </div>
              <div className="text-lg">{day.icon as string}</div>
              <div className="text-xs" style={{ color: 'var(--collie-nose)' }}>
                {day.high as string}° <span style={{ color: 'var(--collie-paw)' }}>{day.low as string}°</span>
              </div>
              {day.rain_chance != null && (day.rain_chance as number) > 20 && (
                <div className="text-xs" style={{ color: 'var(--collie-sky)' }}>
                  {day.rain_chance as string}%
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
