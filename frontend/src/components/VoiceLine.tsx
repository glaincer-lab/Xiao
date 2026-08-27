import { useEffect, useRef } from 'react'

// 真实声线动画：后端通过 WebSocket 推送麦克风 RMS 电平（0~1），
// 这里把历史电平画成一条实时滚动的声线。没声音时是平线，说话时起伏。
export function VoiceLine({ level }: { level: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const levelRef = useRef(0)
  levelRef.current = level

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const HISTORY = 80 // 保留最近 80 个电平点
    const history: number[] = []
    let raf = 0
    let last = performance.now()

    const draw = () => {
      raf = requestAnimationFrame(draw)
      const now = performance.now()
      // 每 ~30ms 采一个点，配合后端 100ms 推送，声线平滑滚动
      if (now - last < 30) return
      last = now

      const w = canvas.width
      const h = canvas.height
      const dpr = window.devicePixelRatio || 1
      if (canvas.width !== canvas.clientWidth * dpr) {
        canvas.width = canvas.clientWidth * dpr
        canvas.height = canvas.clientHeight * dpr
      }

      history.push(levelRef.current)
      if (history.length > HISTORY) history.shift()

      ctx.clearRect(0, 0, canvas.width, canvas.height)
      const mid = canvas.height / 2
      const amp = canvas.height * 0.46 // 最大振幅

      // 中线
      ctx.strokeStyle = 'rgba(120, 170, 255, 0.18)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, mid)
      ctx.lineTo(canvas.width, mid)
      ctx.stroke()

      // 声线
      ctx.strokeStyle = 'rgba(90, 150, 255, 0.9)'
      ctx.lineWidth = 1.6
      ctx.lineJoin = 'round'
      ctx.beginPath()
      for (let i = 0; i < history.length; i++) {
        const x = (i / (HISTORY - 1)) * canvas.width
        const y = mid + (history[i] - 0.5) * amp
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [])

  return <canvas ref={canvasRef} className="voiceline" />
}
