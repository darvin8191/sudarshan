// SUDARSHAN P6 D3: the authenticator's QR, drawn in the browser from the `otpauth://` address the
// service gave. Nothing is sent anywhere to draw it: the secret never leaves this page. Black on
// white with a quiet zone, whatever the theme, because a scanner needs the contrast.
import qrcode from 'qrcode-generator'
import { useMemo } from 'react'

const QUIET = 4

export function QrCode({ value, label }: { value: string; label: string }) {
  const drawn = useMemo(() => {
    try {
      const code = qrcode(0, 'M')
      code.addData(value)
      code.make()
      const size = code.getModuleCount()
      let path = ''
      for (let row = 0; row < size; row += 1) {
        for (let col = 0; col < size; col += 1) {
          if (code.isDark(row, col)) path += `M${col + QUIET} ${row + QUIET}h1v1h-1z`
        }
      }
      return { size: size + QUIET * 2, path }
    } catch {
      return null
    }
  }, [value])
  if (!drawn) return null
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${drawn.size} ${drawn.size}`}
      shapeRendering="crispEdges"
      className="size-52 max-w-full self-center rounded-xl border"
    >
      <rect width={drawn.size} height={drawn.size} fill="#FFFFFF" />
      <path d={drawn.path} fill="#000000" />
    </svg>
  )
}
