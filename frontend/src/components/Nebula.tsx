import { useEffect, useRef } from 'react'
import * as THREE from 'three'

// 状态 → 3D 形态：同一组粒子在不同状态下流成不同立体
type Shape = 'sphere' | 'shell' | 'hyperboloid' | 'spiral' | 'infinity' | 'tetrahedron' | 'octahedron' | 'cube'

const STATE_SHAPE: Record<string, Shape> = {
  listening: 'octahedron', // 八面体（聆听，轻微呼吸）
  processing: 'infinity', // ∞ 无限符号（思考时间长）
  executing: 'spiral', // 3D 螺旋锥
  working: 'spiral',
  speaking: 'hyperboloid', // 收腰圆柱（播报，明显呼吸）
  confirm_shutdown: 'cube',
  await_approval: 'cube',
}

// 状态 → 颜色（同一时刻单色，切换时平滑变）
const STATE_COLORS: Record<string, [number, number, number]> = {
  idle: [74, 125, 255],
  sleeping: [74, 125, 255],
  listening: [52, 211, 153],
  processing: [167, 139, 250],
  speaking: [244, 114, 182],
  executing: [251, 146, 60],
  working: [251, 146, 60],
  confirm_shutdown: [239, 68, 68],
  await_approval: [239, 68, 68],
}

const VERT = `
attribute float aSize;
uniform float uPixelRatio;
uniform float uBreathe;
varying float vDepth;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vDepth = -mv.z;
  gl_PointSize = aSize * uPixelRatio * uBreathe * (18.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`

const FRAG = `
uniform vec3 uColor;
uniform float uOpacity;
uniform float uNear;
uniform float uFar;
varying float vDepth;
void main() {
  vec2 c = gl_PointCoord - vec2(0.5);
  float d = length(c);
  float a = smoothstep(0.5, 0.08, d);
  if (a < 0.01) discard;
  float t = clamp((vDepth - uNear) / (uFar - uNear), 0.0, 1.0);
  float bright = mix(1.0, 0.15, t); // 景深雾化：远处暗
  gl_FragColor = vec4(uColor * bright, a * uOpacity);
}
`

// 同一组归一化种子 (u,v,w) 映射到不同立体，morph 时粒子平滑流动；t 为流动时间
function shapePos(
  shape: Shape,
  u: number,
  v: number,
  w: number,
  R: number,
  t: number,
): [number, number, number] {
  if (shape === 'shell') {
    // 空心球壳：睡眠
    const th = Math.acos(2 * v - 1)
    const ph = Math.PI * 2 * w
    const r = R * Math.cbrt(0.3 + 0.7 * u)
    return [r * Math.sin(th) * Math.cos(ph), r * Math.cos(th), r * Math.sin(th) * Math.sin(ph)]
  }
  if (shape === 'hyperboloid') {
    // 收腰圆柱（单叶双曲面）：播报，绕 y 轴；体积已放大
    const y = (w * 2 - 1) * R * 0.85
    const a = R * 0.62
    const cc = R * 0.78
    const rho = a * Math.sqrt(1 + (y * y) / (cc * cc))
    const rr = rho * Math.sqrt(v)
    const th = Math.PI * 2 * u + t * 0.04
    return [rr * Math.cos(th), y, rr * Math.sin(th)]
  }
  if (shape === 'spiral') {
    // 3D 螺旋锥：干活，粒子沿螺旋上升
    const h = u
    const rad = R * (1 - h * 0.85)
    const rr = rad * Math.sqrt(v)
    const th = Math.PI * 2 * w + 2.6 * Math.PI * 2 * h + t * 0.1
    return [rr * Math.cos(th), (h * 2 - 1) * R, rr * Math.sin(th)]
  }
  if (shape === 'infinity') {
    // ∞ 无限符号（torus knot 2,3）：思考，结平躺在 xz 面
    const tt = Math.PI * 2 * u + t * 0.05
    const p = 2
    const q = 3
    const cq = Math.cos(q * tt)
    const sq = Math.sin(q * tt)
    const cp = Math.cos(p * tt)
    const sp = Math.sin(p * tt)
    const f = 2 + cq
    const x = f * cp
    const y = f * sp
    const z = sq
    const fq = -q * sq
    const dx = fq * cp - p * f * sp
    const dy = fq * sp + p * f * cp
    const dz = q * cq
    let tx = dx
    let ty = dy
    let tz = dz
    const tl = Math.hypot(tx, ty, tz)
    tx /= tl
    ty /= tl
    tz /= tl
    let rx = 0
    let ry = 1
    let rz = 0
    let nx = ry * tz - rz * ty
    let ny = rz * tx - rx * tz
    let nz = rx * ty - ry * tx
    let nl = Math.hypot(nx, ny, nz)
    if (nl < 1e-4) {
      rx = 1
      ry = 0
      rz = 0
      nx = ry * tz - rz * ty
      ny = rz * tx - rx * tz
      nz = rx * ty - ry * tx
      nl = Math.hypot(nx, ny, nz)
    }
    nx /= nl
    ny /= nl
    nz /= nl
    const bx = ty * nz - tz * ny
    const by = tz * nx - tx * nz
    const bz = tx * ny - ty * nx
    const rr = R * 0.18 * Math.sqrt(v)
    const th = Math.PI * 2 * w
    const cth = Math.cos(th)
    const sth = Math.sin(th)
    const s = R * 0.5
    return [
      s * x + rr * (cth * nx + sth * bx),
      R * 0.85 * z + rr * (cth * nz + sth * bz),
      s * y + rr * (cth * ny + sth * by),
    ]
  }
  if (shape === 'octahedron') {
    // 八面体：播报（边自转边扩张收缩）
    const th = Math.acos(2 * v - 1)
    const ph = Math.PI * 2 * w + t * 0.05 // 绕 y 轴自转（接近静止）
    const dx = Math.sin(th) * Math.cos(ph)
    const dy = Math.cos(th)
    const dz = Math.sin(th) * Math.sin(ph)
    const L1 = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)
    const r = R * 1.15 * Math.cbrt(u) / L1
    return [r * dx, r * dy, r * dz]
  }
  if (shape === 'tetrahedron') {
    // 四面体：播报
    const a = Math.min(u, v, w)
    const c = Math.max(u, v, w)
    const b = u + v + w - a - c
    const w0 = a
    const w1 = b - a
    const w2 = c - b
    const w3 = 1 - c
    const V = [[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]]
    const s = R * 0.85
    return [
      s * (w0 * V[0][0] + w1 * V[1][0] + w2 * V[2][0] + w3 * V[3][0]),
      s * (w0 * V[0][1] + w1 * V[1][1] + w2 * V[2][1] + w3 * V[3][1]),
      s * (w0 * V[0][2] + w1 * V[1][2] + w2 * V[2][2] + w3 * V[3][2]),
    ]
  }
  if (shape === 'cube') {
    // 立方体：确认 / 审批（已缩小）
    const L = R * 0.55
    return [(u - 0.5) * 2 * L, (v - 0.5) * 2 * L, (w - 0.5) * 2 * L]
  }
  // sphere：球体内均匀，实心（待机）
  const r = R * Math.cbrt(u)
  const th = Math.acos(2 * v - 1)
  const ph = Math.PI * 2 * w
  return [r * Math.sin(th) * Math.cos(ph), r * Math.cos(th), r * Math.sin(th) * Math.sin(ph)]
}

export function Nebula({ state }: { state: string }) {
  const mountRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return

    const scene = new THREE.Scene()
    const CAM_DIST = 30
    const camera = new THREE.PerspectiveCamera(60, mount.clientWidth / mount.clientHeight, 0.1, 1000)
    camera.position.set(0, 0, CAM_DIST)
    camera.lookAt(0, 0, 0)

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(mount.clientWidth, mount.clientHeight)
    mount.appendChild(renderer.domElement)

    const COUNT = 1200
    const R = 9
    const seeds = new Float32Array(COUNT * 3)
    const sizes = new Float32Array(COUNT)
    const positions = new Float32Array(COUNT * 3)
    const phases = new Float32Array(COUNT)

    for (let i = 0; i < COUNT; i++) {
      const u = Math.random()
      const v = Math.random()
      const w = Math.random()
      seeds[i * 3] = u
      seeds[i * 3 + 1] = v
      seeds[i * 3 + 2] = w
      sizes[i] = 2.5 + Math.random() * 3.5 // 2.5~6.0，匹配项目尺度（相机更远）
      phases[i] = Math.random() * Math.PI * 2
      const [x, y, z] = shapePos('sphere', u, v, w, R, 0)
      positions[i * 3] = x
      positions[i * 3 + 1] = y
      positions[i * 3 + 2] = z
    }

    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1))

    const uniforms = {
      uColor: { value: new THREE.Color(0x4a7dff) },
      uOpacity: { value: 0.92 },
      uBreathe: { value: 1 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      uNear: { value: CAM_DIST - R * 1.6 },
      uFar: { value: CAM_DIST + R * 1.6 },
    }
    const mat = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: VERT,
      fragmentShader: FRAG,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    const points = new THREE.Points(geo, mat)
    scene.add(points)

    const curColor = new THREE.Color(0x4a7dff)
    const targetColor = new THREE.Color()
    const clock = new THREE.Clock()
    let raf = 0
    let time = 0
    let camAngle = 0

    const animate = () => {
      // 用真实流逝秒数（而非每帧固定步长），呼吸/环绕速度与屏幕刷新率无关
      const dt = Math.min(clock.getDelta(), 0.1)
      const s = stateRef.current
      const shape = STATE_SHAPE[s] ?? 'sphere'
      const isActive = ['listening', 'speaking', 'processing', 'executing', 'working'].includes(s)

      // 颜色平滑变
      const tc = STATE_COLORS[s] ?? [74, 125, 255]
      targetColor.setRGB(tc[0] / 255, tc[1] / 255, tc[2] / 255)
      curColor.lerp(targetColor, 0.06)
      uniforms.uColor.value.copy(curColor)

      // 位置流向目标形态（带流动时间 time）
      for (let i = 0; i < COUNT; i++) {
        const u = seeds[i * 3]
        const v = seeds[i * 3 + 1]
        const w = seeds[i * 3 + 2]
        const [tx, ty, tz] = shapePos(shape, u, v, w, R, time)
        positions[i * 3] += (tx - positions[i * 3]) * 0.03
        positions[i * 3 + 1] += (ty - positions[i * 3 + 1]) * 0.03
        positions[i * 3 + 2] += (tz - positions[i * 3 + 2]) * 0.03
      }
      geo.attributes.position.needsUpdate = true

      // 呼吸：只让「光点大小」一收一缩，位置/距离完全不动，避免产生远近感
      // 频率是「弧度/秒」：sin(time*X) 的周期 = 2π/X 秒
      let breathe = 1
      if (shape === 'hyperboloid') {
        breathe = 1 + Math.sin(time * 1.2) * 0.16 // 说话：约 5 秒一收一缩
      } else if (shape === 'octahedron') {
        breathe = 1 + Math.sin(time * 0.8) * 0.05 // 聆听：约 8 秒，轻微平滑
      }
      uniforms.uBreathe.value = breathe

      // 景深范围随相机到原点距离
      uniforms.uNear.value = CAM_DIST - R * 1.6
      uniforms.uFar.value = CAM_DIST + R * 1.6

      // 相机视差环绕：球面飞行（到雕塑距离恒定），俯仰角上下摆动 + 绕 Y 轴旋转
      camAngle += 0.1 * dt
      const t = camAngle
      const elev = 0.5 * Math.sin(2 * t)
      camera.position.set(
        CAM_DIST * Math.cos(elev) * Math.sin(t),
        CAM_DIST * Math.sin(elev),
        CAM_DIST * Math.cos(elev) * Math.cos(t),
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
      uniforms.uPixelRatio.value = Math.min(window.devicePixelRatio, 2)
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
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
    }
  }, [])

  return <div ref={mountRef} className="nebula" />
}
