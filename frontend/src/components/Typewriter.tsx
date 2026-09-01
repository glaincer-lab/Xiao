import { useEffect, useState } from 'react'

// 逐字仅对短文本生效：长文本逐字会造成数秒高频 setState 重渲染，直接一次性显示
const TYPED_MAX = 60

export function Typewriter({ text, speed = 25 }: { text: string; speed?: number }) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!text) {
      setCount(0)
      return
    }
    if (text.length > TYPED_MAX) {
      setCount(text.length)
      return
    }
    setCount(0)
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
