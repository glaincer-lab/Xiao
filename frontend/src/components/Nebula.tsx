import { useEffect, useRef } from 'react'
import * as THREE from 'three'

/** 精灵形态：'auto' 跟随状态自动切换，或手动锁定 0~4 */
export type SpriteMode = 'auto' | 0 | 1 | 2 | 3 | 4

/** 精灵名称（供设置页下拉框展示） */
export const SPRITE_NAMES: Record<number, string> = {
  0: '圆形光点',
  1: '六边晶体',
  2: '蜂巢描边',
  3: '菱晶切片',
  4: '铁环',
}

/** 状态 → 粒子团轮廓形态 */
const STATE_SHAPE: Record<string, string> = {
  listening: 'octahedron',
  processing: 'infinity',
  executing: 'spiral',
  working: 'spiral',
  speaking: 'hyperboloid',
  confirm_shutdown: 'cube',
  await_approval: 'cube',
}

/** 状态 → 精灵序号（sprite='auto' 时生效） */
export const SPRITE_OF: Record<string, number> = {
  sleeping: 0,
  idle: 2,
  listening: 3,
  processing: 1,
  speaking: 1,
  executing: 4,
  working: 4,
  await_approval: 3,
  confirm_shutdown: 4,
}

/** 状态 → 主题色兜底表（CSS 变量读取失败时使用） */
const STATE_COLORS: Record<string, string> = {
  sleeping: '#5a6f97',
  idle: '#7aa2f7',
  listening: '#22d3ee',
  processing: '#a78bfa',
  speaking: '#f59e0b',
  executing: '#34d399',
  working: '#34d399',
  await_approval: '#f472b6',
  confirm_shutdown: '#ef4444',
}

function hexToRgb(hex: string): [number, number, number] {
  const m = hex.replace('#', '')
  const full = m.length === 3 ? m.split('').map((c) => c + c).join('') : m
  const n = parseInt(full, 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

/** 从 CSS 变量读取各状态主题色，读取失败时退回兜底表 */
export function stateColorsFromCss(): Record<string, THREE.Color> {
  const st = getComputedStyle(document.documentElement)
  const out: Record<string, THREE.Color> = {}
  for (const [k, v] of Object.entries(STATE_COLORS)) {
    const css = st.getPropertyValue(`--st-${k}`).trim()
    const rgb = hexToRgb(css || v)
    out[k] = new THREE.Color(rgb[0], rgb[1], rgb[2])
  }
  return out
}

const fract = (x: number): number => x - Math.floor(x)

/* ── 着色器（自 demo 移植）：双色径向渐变 + 微闪/余烬 + 语音增亮 + 景深雾化 ── */
const VERT = `
uniform float uTime,uVoice,uBreathe,uEmber,uSprite;
uniform vec3 uColor0,uColor1;
attribute float aSeed;
varying vec3 vC; varying float vA,vGlow,vTw,vRot;
void main(){
  vec3 p = position*uBreathe;
  float tw = sin(uTime*(1.5+fract(aSeed*7.0)*2.0)+aSeed*6.2832)*0.5+0.5;
  float ember = pow(max(0.0,sin(uTime*0.35+aSeed*91.0)),48.0);
  vTw = mix(0.72+0.28*tw, 0.30+ember*2.2, uEmber);
  vC = mix(uColor0,uColor1,smoothstep(2.5,11.5,length(position)));
  vGlow = 1.0+0.35*uVoice;
  vRot = aSeed*6.2832 + uTime*(0.08+fract(aSeed*5.1)*0.22);
  vec4 mv = modelViewMatrix*vec4(p,1.0);
  float sz = (2.5+fract(aSeed*3.3)*3.5)*(1.0+0.28*uVoice)*(0.85+0.35*vTw);
  sz *= mix(1.0,1.75,step(0.5,uSprite));
  sz *= mix(1.0, 1.15, step(3.5, uSprite));
  gl_PointSize = max(sz*(22.0/-mv.z), 4.6*step(0.5,uSprite));
  gl_Position = projectionMatrix*mv;
  float fog = clamp((-mv.z-15.6)/28.8,0.0,1.0);
  vA = mix(1.0,0.15,fog)*0.92;
}`

const FRAG = `
precision highp float;
uniform float uSprite;
varying vec3 vC; varying float vA,vGlow,vTw,vRot;
/* 正六边形符号距离（尖朝 ±x，外接圆半径 = 参数 d 的等值线） */
float hexD(vec2 p){
  p = abs(p);
  return max(dot(p, vec2(0.5,0.8660254)), p.x);
}
void main(){
  vec2 uv = gl_PointCoord - 0.5;
  float cs = cos(vRot), sn = sin(vRot);
  uv = mat2(cs,sn,-sn,cs) * uv;
  float a = 0.0;
  float rim = 0.0;
  if (uSprite < 0.5) {
    /* 0 圆形：星尘光点 */
    a = smoothstep(0.5, 0.12, length(uv));
  } else if (uSprite < 1.5) {
    /* 1 六边晶体：实心晶体 + 晶面亮边 + 中心亮核 */
    float d = hexD(uv);
    a = smoothstep(0.5, 0.40, d);
    rim = smoothstep(0.20, 0.42, d) * smoothstep(0.55, 0.46, d);
    a += smoothstep(0.16, 0.0, length(uv)) * 0.45;
    rim += smoothstep(0.14, 0.0, length(uv)) * 0.35;
  } else if (uSprite < 2.5) {
    /* 2 蜂巢描边：空心六边形描线 + 中心点（全息图纸感） */
    float d = hexD(uv);
    a = smoothstep(0.085, 0.0, abs(d - 0.38)) * 0.9 + smoothstep(0.13, 0.0, length(uv)) * 0.75;
    rim = 0.30;
  } else if (uSprite < 3.5) {
    /* 3 菱晶：菱形切片 + 亮边 */
    float d = abs(uv.x) + abs(uv.y);
    a = smoothstep(0.5, 0.40, d);
    rim = smoothstep(0.18, 0.42, d) * smoothstep(0.55, 0.46, d);
  } else {
    /* 4 铁环：涡轮仪表环，双弧微光沿环旋转（含中心轮毂光点） */
    float d = abs(length(uv) - 0.34);
    float arc = 0.55 + 0.60 * sin(atan(uv.y, uv.x) * 2.0 + vRot * 2.2);
    a = smoothstep(0.105, 0.0, d) * arc * 1.4;
    a += smoothstep(0.08, 0.0, length(uv)) * 0.55;
    rim = 0.22 + 0.38 * arc;
  }
  vec3 col = vC*vGlow*(0.75+0.45*vTw) + vec3(rim*0.8);
  gl_FragColor = vec4(col, a*vA);
}`

type Shape =
  | 'sphere'
  | 'shell'
  | 'hyperboloid'
  | 'spiral'
  | 'infinity'
  | 'tetrahedron'
  | 'octahedron'
  | 'cube'

/* 同一组归一化种子 (u,v,w) → 不同立体（demo 版 + 旧版四面体），t 为流动时间 */
function shapePos(shape: Shape, u: number, v: number, w: number, R: number, t: number): [number, number, number] {
  if (shape === 'shell') {
    /* 空心薄壳 */
    const th = Math.acos(2 * v - 1)
    const ph = Math.PI * 2 * w
    const r = R * 1.06
    return [r * Math.sin(th) * Math.cos(ph), r * Math.cos(th), r * Math.sin(th) * Math.sin(ph)]
  }
  if (shape === 'hyperboloid') {
    /* 收腰圆柱（单叶双曲面）：播报 */
    const y = (w * 2 - 1) * R * 0.85
    const a = R * 0.62
    const cc = R * 0.78
    const rho = a * Math.sqrt(1 + (y * y) / (cc * cc))
    const rr = rho * Math.sqrt(v)
    const th = Math.PI * 2 * u + t * 0.04
    return [rr * Math.cos(th), y, rr * Math.sin(th)]
  }
  if (shape === 'spiral') {
    /* 螺旋锥：干活 */
    const h = u
    const rad = R * (1 - h * 0.85)
    const rr = rad * Math.sqrt(v)
    const th = Math.PI * 2 * w + 2.6 * Math.PI * 2 * h + t * 0.1
    return [rr * Math.cos(th), (h * 2 - 1) * R, rr * Math.sin(th)]
  }
  if (shape === 'infinity') {
    /* ∞ 无限符号（环面结 2,3）：思考 */
    const tt = Math.PI * 2 * u + t * 0.05
    const f = 2 + Math.cos(3 * tt)
    const x = f * Math.cos(2 * tt)
    const y = f * Math.sin(2 * tt)
    const z = Math.sin(3 * tt)
    const th = Math.PI * 2 * w
    const rr = R * 0.18 * Math.sqrt(v)
    const s = R * 0.5
    return [s * x + rr * Math.cos(th), R * 0.85 * z + rr * Math.sin(th), s * y]
  }
  if (shape === 'octahedron') {
    /* 八面体：聆听，边自转边扩张收缩 */
    const th = Math.acos(2 * v - 1)
    const ph = Math.PI * 2 * w + t * 0.05
    const dx = Math.sin(th) * Math.cos(ph)
    const dy = Math.cos(th)
    const dz = Math.sin(th) * Math.sin(ph)
    const L1 = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)
    const r = (R * 1.15 * Math.cbrt(u)) / L1
    return [r * dx, r * dy, r * dz]
  }
  if (shape === 'tetrahedron') {
    /* 四面体（旧版保留）：按 min/mid/max 重排做重心坐标组合 */
    const a = Math.min(u, v, w)
    const c = Math.max(u, v, w)
    const b = u + v + w - a - c
    const w0 = a
    const w1 = b - a
    const w2 = c - b
    const w3 = 1 - c
    const V = [
      [1, 1, 1],
      [1, -1, -1],
      [-1, 1, -1],
      [-1, -1, 1],
    ]
    const s = R * 0.85
    return [
      s * (w0 * V[0][0] + w1 * V[1][0] + w2 * V[2][0] + w3 * V[3][0]),
      s * (w0 * V[0][1] + w1 * V[1][1] + w2 * V[2][1] + w3 * V[3][1]),
      s * (w0 * V[0][2] + w1 * V[1][2] + w2 * V[2][2] + w3 * V[3][2]),
    ]
  }
  if (shape === 'cube') {
    /* 立方体：确认 / 审批 */
    const L = R * 0.55
    return [(u - 0.5) * 2 * L, (v - 0.5) * 2 * L, (w - 0.5) * 2 * L]
  }
  /* sphere：球体内均匀（待机 / 睡眠兜底） */
  const r = R * Math.cbrt(u)
  const th = Math.acos(2 * v - 1)
  const ph = Math.PI * 2 * w
  return [r * Math.sin(th) * Math.cos(ph), r * Math.cos(th), r * Math.sin(th) * Math.sin(ph)]
}

/* 立方体 / 八面体棱线表（流光沿棱走；TS 布尔不能相加，用三元取 0/1） */
const CUBE_V: number[][] = []
const CUBE_E: number[][] = []
for (let i = 0; i < 8; i++) CUBE_V.push([i & 1 ? 1 : -1, i & 2 ? 1 : -1, i & 4 ? 1 : -1])
for (let i = 0; i < 8; i++)
  for (let j = i + 1; j < 8; j++) {
    const d =
      (CUBE_V[i][0] !== CUBE_V[j][0] ? 1 : 0) +
      (CUBE_V[i][1] !== CUBE_V[j][1] ? 1 : 0) +
      (CUBE_V[i][2] !== CUBE_V[j][2] ? 1 : 0)
    if (d === 1) CUBE_E.push([i, j])
  }
const OCT_V = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
]
const OCT_E: number[][] = []
for (let i = 0; i < 6; i++)
  for (let j = i + 1; j < 6; j++) {
    const dot = OCT_V[i][0] * OCT_V[j][0] + OCT_V[i][1] * OCT_V[j][1] + OCT_V[i][2] * OCT_V[j][2]
    if (dot === 0) OCT_E.push([i, j])
  }

/* 流光透明度：各态基础值（睡眠几乎无流光） */
const TRAV_OP: Record<string, number> = {
  sleeping: 0.16,
  idle: 0.5,
  listening: 0.8,
  processing: 0.9,
  speaking: 0.95,
  executing: 0.95,
  working: 0.95,
  await_approval: 1,
  confirm_shutdown: 1,
}

/* 生命值曲线（demo lifeVal 移植）：波形与光球同源的「生命力」节拍 */
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

/* 流光条目：prog 为 0~1 相位（主循环按 dt 增量推进，永不错拍） */
type Trav = {
  s0: number
  s1: number
  s2: number
  b1: boolean
  prog: number
  speed: number
  head: THREE.Vector3
  prev: THREE.Vector3
  col: THREE.Color
}

/* 流光路径：每态一种「能量走法」；time 只负责整体缓慢旋转 */
function travTarget(tv: Trav, shape: Shape, R: number, t: number, out: THREE.Vector3) {
  const s0 = tv.s0
  const s1 = tv.s1
  const s2 = tv.s2
  const dir = tv.b1 ? 1 : -1
  const p = tv.prog % 1
  if (shape === 'cube' || shape === 'octahedron') {
    const V = shape === 'cube' ? CUBE_V : OCT_V
    const E = shape === 'cube' ? CUBE_E : OCT_E
    const L = shape === 'cube' ? R * 0.55 : R * 1.15
    const e = E[Math.floor(s0 * E.length) % E.length]
    const ph = fract(s1 + p * dir) /* 沿棱往返：回卷两端重合，无跳变 */
    const pp = ph < 0.5 ? ph * 2 : 2 - ph * 2
    const a = V[e[0]]
    const b = V[e[1]]
    out.set((a[0] + (b[0] - a[0]) * pp) * L, (a[1] + (b[1] - a[1]) * pp) * L, (a[2] + (b[2] - a[2]) * pp) * L)
    return
  }
  if (shape === 'hyperboloid') {
    /* 垂直上升流（整体缓慢旋转） */
    const th = s0 * Math.PI * 2 + t * 0.15
    const y = fract(s1 + p * dir)
    const yy = (y * 2 - 1) * R * 0.85
    const a = R * 0.62
    const cc = R * 0.78
    const rho = a * Math.sqrt(1 + (yy * yy) / (cc * cc))
    out.set(rho * Math.cos(th), yy, rho * Math.sin(th))
    return
  }
  if (shape === 'spiral') {
    /* 螺旋攀升 */
    const h = fract(s1 + p * dir)
    const rad = R * (1 - h * 0.85)
    const rr = rad * Math.sqrt(s2)
    const th = Math.PI * 2 * s0 + 2.6 * Math.PI * 2 * h + t * 0.1
    out.set(rr * Math.cos(th), (h * 2 - 1) * R, rr * Math.sin(th))
    return
  }
  if (shape === 'infinity') {
    /* 沿无穷符号闭合曲线（回卷连续） */
    const tt = Math.PI * 2 * (s0 + p * dir) + t * 0.05
    const f = 2 + Math.cos(3 * tt)
    const x = f * Math.cos(2 * tt)
    const y = f * Math.sin(2 * tt)
    const z = Math.sin(3 * tt)
    out.set(R * 0.5 * x, R * 0.85 * z, R * 0.5 * y)
    return
  }
  /* sphere / shell / 其余：倾斜大圆轨道（闭合，回卷连续） */
  const r = shape === 'shell' ? R * 1.06 : R * (0.45 + 0.45 * s2)
  const tilt = s2 * Math.PI + 1.7 * s1
  const th = s0 * Math.PI * 2 + Math.PI * 2 * p * dir + t * 0.05
  out.set(r * Math.cos(th), r * Math.sin(th) * Math.cos(tilt), r * Math.sin(th) * Math.sin(tilt))
}

/** 三维星云：粒子精灵 + 流光流星 + 切态收拢绽放
 *  sprite：'auto' 跟随状态自动切换，或手动锁定 0~4；level：聆听态麦克风电平（0~1） */
export function Nebula({
  state,
  sprite = 'auto',
  level = 0,
}: {
  state: string
  sprite?: SpriteMode
  level?: number
}) {
  const mountRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef(state)
  const spriteRef = useRef<SpriteMode>(sprite)
  const levelRef = useRef(level)
  stateRef.current = state
  spriteRef.current = sprite
  levelRef.current = level

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return

    const scene = new THREE.Scene()
    const CAM_DIST = 30
    const R = 9
    const camera = new THREE.PerspectiveCamera(60, mount.clientWidth / mount.clientHeight, 0.1, 1000)
    camera.position.set(0, 0, CAM_DIST)
    camera.lookAt(0, 0, 0)

    // 低性能模式：CPU 核心少 → 减粒子、关抗锯齿、降像素比；系统「减少动态效果」→ 停环绕/呼吸/流光
    const lowPerf = navigator.hardwareConcurrency <= 4 || !navigator.hardwareConcurrency
    const reducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const maxRatio = lowPerf ? 1 : 2

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: !lowPerf })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, maxRatio))
    renderer.setSize(mount.clientWidth, mount.clientHeight)
    mount.appendChild(renderer.domElement)

    /* 粒子团：种子决定形态归属与尺寸/相位 */
    const COUNT = lowPerf ? 500 : 1200
    const seeds = new Float32Array(COUNT * 3)
    const seedAttr = new Float32Array(COUNT)
    const positions = new Float32Array(COUNT * 3)
    for (let i = 0; i < COUNT; i++) {
      seeds[i * 3] = Math.random()
      seeds[i * 3 + 1] = Math.random()
      seeds[i * 3 + 2] = Math.random()
      seedAttr[i] = Math.random()
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geo.setAttribute('aSeed', new THREE.BufferAttribute(seedAttr, 1))
    const uniforms = {
      uTime: { value: 0 },
      uVoice: { value: 0 },
      uBreathe: { value: 1 },
      uEmber: { value: 0 },
      uSprite: { value: 2 },
      uColor0: { value: new THREE.Color() },
      uColor1: { value: new THREE.Color() },
    }
    const mat = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: VERT,
      fragmentShader: FRAG,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    scene.add(new THREE.Points(geo, mat))

    /* 流光：短促流星群 */
    const TRAV_N = lowPerf ? 28 : 56
    const trav: Trav[] = []
    for (let i = 0; i < TRAV_N; i++)
      trav.push({
        s0: Math.random(),
        s1: Math.random(),
        s2: Math.random(),
        b1: Math.random() < 0.5,
        prog: Math.random(),
        speed: 0.55 + Math.random() * 0.85,
        head: new THREE.Vector3(),
        prev: new THREE.Vector3(),
        col: new THREE.Color(),
      })
    const travPos = new Float32Array(TRAV_N * 6)
    const travCol = new Float32Array(TRAV_N * 6)
    const travGeo = new THREE.BufferGeometry()
    travGeo.setAttribute('position', new THREE.BufferAttribute(travPos, 3))
    travGeo.setAttribute('color', new THREE.BufferAttribute(travCol, 3))
    const travMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.4,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
    scene.add(new THREE.LineSegments(travGeo, travMat))

    const tmpT = new THREE.Vector3()
    const tmpV = new THREE.Vector3()
    const tmpV2 = new THREE.Vector3()
    const tmpC = new THREE.Color()
    const WHITE = new THREE.Color(1, 1, 1)
    const stateColors = stateColorsFromCss()
    const cur0 = new THREE.Color()
    const cur1 = new THREE.Color()
    const tgt0 = new THREE.Color()
    const tgt1 = new THREE.Color()

    let camAngle = 0
    let dipT = 1 /* 切态收拢绽放进度（状态变化时归零激活） */
    let lastStateKey: string | null = null
    let voice = 0
    let synthNext = 0 /* 播报态合成音节包络：下一音节起始时刻 */
    let synthTgt = 0 /* 当前音节目标幅度 */

    const clock = new THREE.Clock()
    let raf = 0
    let time = 0

    const animate = () => {
      // 用真实流逝秒数（而非每帧固定步长），呼吸/环绕速度与屏幕刷新率无关
      const dt = Math.min(clock.getDelta(), 0.1)
      const s = stateRef.current
      const shape = (STATE_SHAPE[s] ?? 'sphere') as Shape

      /* 切态：激活 dip 收拢绽放 + 流光立即落位到新形态（首帧即入场绽放） */
      if (s !== lastStateKey) {
        lastStateKey = s
        dipT = 0
        for (const tv of trav) {
          travTarget(tv, shape, R, time, tmpT)
          tv.head.copy(tmpT)
          tv.prev.copy(tmpT)
        }
      }

      /* 精灵形态：'auto' 跟随状态，或手动锁定（每帧接线，选择即时生效） */
      const sm = spriteRef.current
      uniforms.uSprite.value = sm === 'auto' ? SPRITE_OF[s] ?? 0 : sm
      uniforms.uTime.value = time

      /* 语音包络 uVoice：聆听用真实麦克风电平（留底噪），播报用合成音节包络（TTS 不回采麦克风） */
      let vTgt = 0
      if (s === 'listening') {
        vTgt = Math.min(1, Math.max(levelRef.current, 0.06))
      } else if (s === 'speaking') {
        if (time >= synthNext) {
          const dur = 0.1 + Math.random() * 0.16
          synthNext =
            time + dur + (Math.random() < 0.22 ? 0.15 + Math.random() * 0.15 : 0.03 + Math.random() * 0.05)
          synthTgt = (0.55 + Math.random() * 0.45) * (Math.random() < 0.1 ? 0.25 : 1)
        }
        vTgt = synthTgt
      }
      const vk = 1 - Math.exp(-dt / (vTgt > voice ? 0.05 : 0.14))
      voice += (vTgt - voice) * vk
      uniforms.uVoice.value = voice

      /* 生命值 → 整团呼吸缩放；睡眠态开余烬闪烁 */
      const life = reducedMotion ? 0.55 : lifeOf(s, time, voice)
      uniforms.uBreathe.value = reducedMotion ? 1 : 0.86 + life * 0.32
      uniforms.uEmber.value = s === 'sleeping' ? 1 : 0

      /* 颜色：外缘主色 + 内芯提亮色，0.22s 平滑过渡 */
      const base = stateColors[s] ?? stateColors.idle
      tgt1.copy(base)
      tgt0.copy(base).lerp(WHITE, 0.45)
      const ck = 1 - Math.exp(-dt / 0.22)
      cur0.lerp(tgt0, ck)
      cur1.lerp(tgt1, ck)
      uniforms.uColor0.value.copy(cur0)
      uniforms.uColor1.value.copy(cur1)

      /* 切态收拢绽放：0.45s 内整体先收后放（重要态收得更重）；位置直接落位，形态随 time 流动 */
      dipT += dt
      const dipProg = Math.min(dipT / 0.45, 1)
      const heavy = s === 'await_approval' || s === 'confirm_shutdown' ? 0.42 : 0.28
      const sc = 1 - heavy * Math.sin(dipProg * Math.PI)
      for (let i = 0; i < COUNT; i++) {
        const p = shapePos(shape, seeds[i * 3], seeds[i * 3 + 1], seeds[i * 3 + 2], R, time)
        positions[i * 3] = p[0] * sc
        positions[i * 3 + 1] = p[1] * sc
        positions[i * 3 + 2] = p[2] * sc
      }
      geo.attributes.position.needsUpdate = true

      /* 流光推进：亮度耦合生命值节拍，速度耦合语音包络 */
      const opTgt = reducedMotion ? 0 : (TRAV_OP[s] ?? 0.9) * 0.75 * (0.55 + 0.9 * life)
      travMat.opacity += (opTgt - travMat.opacity) * (1 - Math.exp(-dt / 0.4))
      const spd = 0.22 * (1 + voice * 1.8)
      for (let i = 0; i < trav.length; i++) {
        const tv = trav[i]
        tv.prog = (tv.prog + dt * tv.speed * spd) % 1 /* 增量相位：只影响流速，不产生跳位 */
        travTarget(tv, shape, R, time, tmpT)
        tmpT.multiplyScalar(sc)
        tv.prev.copy(tv.head)
        tv.head.lerp(tmpT, 0.55)
        tmpV.subVectors(tv.head, tv.prev)
        if (tmpV.length() > R * 0.5) tmpV2.copy(tv.head)
        else tmpV2.copy(tv.head).addScaledVector(tmpV, -1.5)
        travPos[i * 6] = tv.head.x
        travPos[i * 6 + 1] = tv.head.y
        travPos[i * 6 + 2] = tv.head.z
        travPos[i * 6 + 3] = tmpV2.x
        travPos[i * 6 + 4] = tmpV2.y
        travPos[i * 6 + 5] = tmpV2.z
        tmpC.copy(cur1).lerp(WHITE, 0.28)
        tv.col.lerp(tmpC, 0.15)
        travCol[i * 6] = tv.col.r
        travCol[i * 6 + 1] = tv.col.g
        travCol[i * 6 + 2] = tv.col.b
        travCol[i * 6 + 3] = tv.col.r * 0.3
        travCol[i * 6 + 4] = tv.col.g * 0.3
        travCol[i * 6 + 5] = tv.col.b * 0.3
      }
      travGeo.attributes.position.needsUpdate = true
      travGeo.attributes.color.needsUpdate = true

      /* 相机球面环绕：到雕塑距离恒定，俯仰角上下摆动 */
      if (!reducedMotion) camAngle += 0.1 * dt
      const elev = 0.5 * Math.sin(2 * camAngle)
      camera.position.set(
        CAM_DIST * Math.cos(elev) * Math.sin(camAngle),
        CAM_DIST * Math.sin(elev),
        CAM_DIST * Math.cos(elev) * Math.cos(camAngle),
      )
      camera.lookAt(0, 0, 0)

      renderer.render(scene, camera)
      time += dt
      raf = requestAnimationFrame(animate)
    }
    animate()

    const onResize = () => {
      const w = mount.clientWidth
      const h = mount.clientHeight
      if (w <= 0 || h <= 0) return
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
    window.addEventListener('resize', onResize)
    // 监听中间列尺寸变化（调整左列宽度时），让 3D 图案和文字/波形始终对齐
    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(onResize)
      ro.observe(mount)
    }

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      if (ro) ro.disconnect()
      geo.dispose()
      mat.dispose()
      travGeo.dispose()
      travMat.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
    }
  }, [])

  return <div ref={mountRef} className="nebula" />
}
