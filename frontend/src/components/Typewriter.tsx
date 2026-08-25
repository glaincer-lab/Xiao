import { useEffect, useState } from 'react'

export function Typewriter({ text, speed = 25 }: { text: string; speed?: number }) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    setCount(0)
    if (!text) return
    let i = 0
    const id = window.setInterval(() => {
      i += 1
      setCount(Math.min(i, text.length))
      if (i >= text.length) window.clearInterval(id)
    }, speed)
    return () => window.clearInterval(id)
  }, [text, speed])

  return <span>{text.slice(0, count)}</span>
}
