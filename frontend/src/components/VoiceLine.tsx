import { useEffect, useRef } from 'react'
import { stateColorsFromCss } from './Nebula'

/* 生命值曲线（与 Nebula 同源）：波形与光球共用同一套「生命力」节拍 */
function lifeOf(s: string, t: number, env: number): number {
  switch (s) {
    case 'sleeping':
      return 0.5 + Math.sin((t * Math.PI * 2) / 14) * 0.1
    case 'idle':
      return 0.5 + Math.sin((t * Math.PI * 2) / 14) * 0.25
    case 'listening':
      return 0.5 + Math.sin((t * Math.PI * 2) / 8) * 0.25
    case 'processing':
      return 0.3 + Math.pow(Math.abs(Math.sin((t * Math.PI * 2) / 5)), 1.5) * 0.55
    case 'speaking':
      return 0.25 + env * 0.9
    case 'executing':
    case 'working':
      return 0.5 + Math.sin((t * Math.PI * 2) / 8) * 0.225
    case 'await_approval': {
      const ph = (t % 2.5) / 2.5 /* 急促双跳 */
      return 0.15 + 0.75 * (Math.exp(-ph * 7) * 0.9 + Math.exp(-Math.max(0, ph - 0.32) * 9) * 0.6)
    }
    case 'confirm_shutdown': {
      const ph = (t % 4) / 4 /* 沉重搏动 */
      return 0.2 + Math.pow(Math.sin(ph * Math.PI), 2) * 0.6
    }
  }
  return 0.5
}

/* 各态底噪包络区间 [低, 高]：非聆听/播报态也保持微弱起伏，避免声纹完全静止 */
const ENV_MAP: Record<string, [number, number]> = {
  sleeping: [0.02, 0.06],
  idle: [0.02, 0.1],
  processing: [0.05, 0.18],
  executing: [0.06, 0.2],
  working: [0.06, 0.2],
  await_approval: [0.05, 0.62],
  confirm_shutdown: [0.04, 0.34],
}

// 曜钢声纹：64 根圆角渐变声柱 + 中线镜像倒影 + 前景尘埃。
// 后端通过 WebSocket 推送麦克风 RMS 电平（0~1），这里平滑成包络驱动整条波形；
// 颜色读 styles.css 的 --st-* 令牌（与 3D 光球同一份事实源），柱色在状态色与其亮化版之间插值。
export function VoiceLine({ level, state = 'idle' }: { level: number; state?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const levelRef = useRef(0)
  levelRef.current = level
  const stateRef = useRef(state)
  stateRef.current = state

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // 系统「减少动态效果」：停行波/尘埃漂移，电平驱动的柱高仍实时反馈
    const prefersReduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches

    // 状态双色：当前状态色（暗端）+ 其亮化版（亮端），切换时平滑过渡
    let colA: [number, number, number] = [74, 125, 255]
    let colB: [number, number, number] = [160, 190, 255]
    let colFrom: [number, number, number] = [...colA]
    let colTo: [number, number, number] = [...colB]
    const readStateColors = (s: string) => {
      const table = stateColorsFromCss()
      // stateColorsFromCss 返回 THREE.Color（0~1），转成 0~255 的 RGB 元组，避免 base[0] 取到 undefined 变 NaN
      const c = table[s] ?? table.idle
      const base: [number, number, number] = [
        Math.round(c.r * 255),
        Math.round(c.g * 255),
        Math.round(c.b * 255),
      ]
      colA = base
      colB = [
        Math.round(base[0] + (255 - base[0]) * 0.55),
        Math.round(base[1] + (255 - base[1]) * 0.55),
        Math.round(base[2] + (255 - base[2]) * 0.55),
      ]
    }
    readStateColors(stateRef.current)
    let lastState = stateRef.current

    const BARS = 64
    const bars = new Float32Array(BARS)
    const bandJit = new Float32Array(BARS)
    // 前景尘埃：随声音能量显隐，围绕中线漂浮
    const parts = Array.from({ length: 40 }, () => ({
      x: Math.random(),
      y: Math.random() - 0.5,
      r: 0.8 + Math.random() * 2.2,
      sp: 0.05 + Math.random() * 0.12,
      ph: Math.random() * Math.PI * 2,
    }))

    let env = 0 // 包络：电平平滑值，驱动整条波形的能量
    let w = 0
    let h = 0
    // 合成音节状态机（demo 移植）：phrase 级调制 + 齿音，让播报/聆听波形贴合说话节奏
    const sched = {
      nextAt: 0, sylDur: 0.2, sylPeak: 0.8,
      phrasePos: 0, phraseLen: 6, phraseAmp: 0.85,
      sibilant: 0, sibUntil: 0,
    }
    function scheduleSpeech(now: number, mode: 'listening' | 'speaking') {
      if (now < sched.nextAt) return
      sched.sylDur = 0.1 + Math.random() * 0.16
      sched.nextAt =
        now + sched.sylDur + (Math.random() < 0.22 ? 0.15 + Math.random() * 0.15 : 0.03 + Math.random() * 0.05)
      sched.phrasePos++
      if (sched.phrasePos >= sched.phraseLen) {
        sched.nextAt = now + sched.sylDur + 0.4 + Math.random() * 0.5
        sched.phrasePos = 0
        sched.phraseLen = 4 + (Math.random() * 5 | 0)
        sched.phraseAmp = 0.55 + Math.random() * 0.4
      }
      sched.sylPeak = sched.phraseAmp * (0.5 + Math.random() * 0.5) * (mode === 'listening' ? 0.78 : 1)
      if (Math.random() < 0.1) sched.sibUntil = now + sched.sylDur
    }
    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const cw = canvas.clientWidth
      const ch = canvas.clientHeight
      if (cw <= 0 || ch <= 0) return false
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr)
        canvas.height = Math.round(ch * dpr)
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      w = cw
      h = ch
      return true
    }

    // roundRect 兜底：旧内核无 ctx.roundRect 时用手动圆角路径
    const rr = (x: number, y: number, w2: number, h2: number, rad: number) => {
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(x, y, w2, h2, rad)
      } else {
        const r = Math.min(rad, w2 / 2, h2 / 2)
        ctx.moveTo(x + r, y)
        ctx.arcTo(x + w2, y, x + w2, y + h2, r)
        ctx.arcTo(x + w2, y + h2, x, y + h2, r)
        ctx.arcTo(x, y + h2, x, y, r)
        ctx.arcTo(x, y, x + w2, y, r)
        ctx.closePath()
      }
    }

    // 柱色：越靠右越接近亮色端，随时间轻微往返摆动
    const barColor = (i: number, t: number): [number, number, number] => {
      const tt = Math.min(1, Math.max(0, i / (BARS - 1) + Math.sin(t * 0.4) * 0.12))
      return [
        Math.round(colFrom[0] + (colTo[0] - colFrom[0]) * tt),
        Math.round(colFrom[1] + (colTo[1] - colFrom[1]) * tt),
        Math.round(colFrom[2] + (colTo[2] - colFrom[2]) * tt),
      ]
    }

    let raf = 0
    let last = performance.now()
    let t = 0
    let tc = 0 // 绝对时钟：合成音节与生命值节拍无条件推进，reduced-motion 下播报波形也不静止

    const draw = () => {
      raf = requestAnimationFrame(draw)
      const now = performance.now()
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      if (!fit()) return
      tc += dt
      if (!prefersReduced) t += dt

      // 状态切换 → 重读令牌；两端颜色向新值平滑靠拢
      const s = stateRef.current
      if (s !== lastState) {
        lastState = s
        readStateColors(s)
      }
      const kCol = 1 - Math.exp(-dt / 0.25)
      for (let c = 0; c < 3; c++) {
        colFrom[c] += (colA[c] - colFrom[c]) * kCol
        colTo[c] += (colB[c] - colTo[c]) * kCol
      }

      // 包络目标（demo 节奏移植）：
      // 播报：合成音节引擎（TTS 不回采麦克风，只能靠合成节奏）
      // 聆听：真实麦克风电平放大增益（后端 rms/8000 偏小，这里 ×5 让说话时波形明显），无信号时退回微弱合成底噪
      // 其余态：底噪 + 生命值基线，保证待机/思考/干活也有节拍起伏
      let envTgt = 0
      if (s === 'speaking') {
        scheduleSpeech(tc, 'speaking')
        envTgt = sched.sylPeak
      } else if (s === 'listening') {
        const mic = Math.min(1, levelRef.current * 5)
        if (levelRef.current > 0.04) {
          envTgt = Math.max(mic, 0.1)
        } else {
          scheduleSpeech(tc, 'listening')
          envTgt = 0.05 + sched.sylPeak * 0.2
        }
      } else {
        const r = ENV_MAP[s] ?? ENV_MAP.idle
        envTgt = r[0] + r[1] * lifeOf(s, tc, env)
      }
      sched.sibilant =
        s === 'listening' || s === 'speaking'
          ? tc < sched.sibUntil
            ? 1
            : Math.max(0, sched.sibilant - dt * 6)
          : 0
      const kEnv = 1 - Math.exp(-dt / (envTgt > env ? 0.045 : 0.16))
      env += (envTgt - env) * kEnv

      const midY = h * 0.4
      const maxAmp = h * 0.42
      const bw = 3.5
      const gap = (w - BARS * bw) / (BARS - 1)
      ctx.clearRect(0, 0, w, h)

      // 前景尘埃
      for (const p of parts) {
        const px = p.x * w + Math.sin(t * p.sp * 2 + p.ph) * 26
        const py = midY + p.y * h * 0.62 + Math.cos(t * p.sp * 1.6 + p.ph * 1.3) * 14
        const near = 1 - Math.min(1, Math.abs(p.y) * 1.8)
        const a = Math.min(0.5, (0.08 + env * 0.9) * (0.05 + near * 0.28))
        const mix = [
          Math.round(colFrom[0] + (colTo[0] - colFrom[0]) * p.x),
          Math.round(colFrom[1] + (colTo[1] - colFrom[1]) * p.x),
          Math.round(colFrom[2] + (colTo[2] - colFrom[2]) * p.x),
        ]
        ctx.fillStyle = `rgba(${mix[0]},${mix[1]},${mix[2]},${a * 0.35})`
        ctx.beginPath()
        ctx.arc(px, py, p.r * 2.4, 0, 6.2832)
        ctx.fill()
        ctx.fillStyle = `rgba(${mix[0]},${mix[1]},${mix[2]},${a})`
        ctx.beginPath()
        ctx.arc(px, py, p.r, 0, 6.2832)
        ctx.fill()
      }

      // 64 根声柱：中心能量高、两端高频颗粒重；行波让能量从左往右流过
      for (let i = 0; i < BARS; i++) {
        const c = barColor(i, t)
        const center = 1 - Math.abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2)
        const bandBase = 0.28 + center * 0.72
        const hfNoise = (1 - center) * (Math.random() - 0.5) * 0.9
        bandJit[i] += (hfNoise - bandJit[i]) * Math.min(1, dt * 22)
        const sib = sched.sibilant
        const bandMix = bandBase * (1 - sib) + (1 - center) * 0.85 * sib
        let target = env * bandMix * (0.72 + 0.28 * Math.sin(t * 5.2 - i * 0.38))
        target += env * bandJit[i] * 0.5
        target = Math.max(0, Math.min(1, target))
        const k = 1 - Math.exp(-dt / (target > bars[i] ? 0.028 : 0.085))
        bars[i] += (target - bars[i]) * k

        const amp = Math.max(bw * 0.55, bars[i] * maxAmp)
        const x = i * (bw + gap)
        const y0 = midY - amp
        const barH = amp * 2
        const g = ctx.createLinearGradient(0, y0, 0, y0 + barH)
        g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},0.16)`)
        g.addColorStop(0.5, `rgba(${c[0]},${c[1]},${c[2]},0.92)`)
        g.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0.16)`)
        ctx.fillStyle = g
        ctx.beginPath()
        rr(x, y0, bw, barH, bw / 2)
        ctx.fill()
        // 峰值高亮芯：能量过半时柱心泛白
        if (bars[i] > 0.5) {
          ctx.fillStyle = `rgba(255,255,255,${(bars[i] - 0.5) * 0.5})`
          ctx.beginPath()
          rr(x + 0.6, midY - amp * 0.4, bw - 1.2, amp * 0.8, (bw - 1.2) / 2)
          ctx.fill()
        }
        // 倒影层：中线下方镜像渐隐，暗玻璃落地感
        if (bars[i] > 0.05) {
          const rh = amp * 0.55
          const g2 = ctx.createLinearGradient(0, midY + 4, 0, midY + 4 + rh)
          g2.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},0.14)`)
          g2.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`)
          ctx.fillStyle = g2
          ctx.fillRect(x, midY + 4, bw, rh)
        }
      }

      // 中线
      const mc = barColor(BARS >> 1, t)
      ctx.strokeStyle = `rgba(${mc[0]},${mc[1]},${mc[2]},0.12)`
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, midY)
      ctx.lineTo(w, midY)
      ctx.stroke()
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [])

  return <canvas ref={canvasRef} className="voiceline" />
}
