/* 生命值曲线（demo lifeVal 移植）：波形（VoiceLine）与光球（Nebula）共用同一套「生命力」节拍，
 * 保证两者随状态起伏完全同步。s=状态，t=绝对时钟秒，env=当前语音/包络能量（0~1）。 */
export function lifeOf(s: string, t: number, env: number): number {
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
