import { useEffect, useRef, useState } from 'react'
import type { PortraitFrame } from './portraitStates'

interface Props {
  frame: PortraitFrame
  fallbackSrc: string
  className?: string
}

/**
 * Draws either one face image or one cell from a face-only source strip.
 * Keeping strip cropping here avoids CSS transforms and keeps full-body atlas
 * geometry out of the composer renderer.
 */
export default function ColliePortraitFrame({ frame, fallbackSrc, className }: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    setFailed(false)
    const image = new Image()
    image.onload = () => {
      const canvas = canvasRef.current
      if (!canvas) return
      const columns = frame.columns || 1
      const rows = frame.rows || 1
      const sourceIndex = frame.sourceIndex || 0
      const sourceWidth = image.naturalWidth / columns
      const sourceHeight = image.naturalHeight / rows
      const outputSize = 384
      const devicePixelRatio = window.devicePixelRatio || 1
      const fit = Math.min(outputSize / sourceWidth, outputSize / sourceHeight)
      const destinationWidth = sourceWidth * fit
      const destinationHeight = sourceHeight * fit
      const destinationX = (outputSize - destinationWidth) / 2
      const destinationY = (outputSize - destinationHeight) / 2
      canvas.width = outputSize * devicePixelRatio
      canvas.height = outputSize * devicePixelRatio
      const context = canvas.getContext('2d')
      if (!context) return
      context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0)
      context.clearRect(0, 0, outputSize, outputSize)
      context.drawImage(
        image,
        (sourceIndex % columns) * sourceWidth,
        Math.floor(sourceIndex / columns) * sourceHeight,
        sourceWidth,
        sourceHeight,
        destinationX,
        destinationY,
        destinationWidth,
        destinationHeight
      )
      setLoaded(true)
    }
    image.onerror = () => setFailed(true)
    image.src = frame.src
    return () => {
      image.onload = null
      image.onerror = null
    }
  }, [frame.columns, frame.rows, frame.sourceIndex, frame.src])

  return (
    <>
      {(failed || !loaded) && <img className={className} src={fallbackSrc} alt="" draggable={false} />}
      {!failed && (
        <canvas
          ref={canvasRef}
          className={className}
          aria-hidden="true"
          style={{ visibility: loaded ? 'visible' : 'hidden' }}
        />
      )}
    </>
  )
}
