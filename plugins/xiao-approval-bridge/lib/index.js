/**
 * 小二（Xiao）语音审批桥 —— DSH Host-only 插件（持久化版）
 *
 * 挂在 DSH 的 `approval/request` 瀑布上，作为 answerer：
 *   1) 读环境变量 XIAO_GRANT（小二后端预授权清单，JSON 数组），命中的归类直接放行；
 *   2) 否则把审批请求转发给小二后端（Python，127.0.0.1:8123）做语音确认。
 *
 * 结果词表与 DSH approval 服务一致：
 *   'allowed-once'（唯一放行）/ 'rejected' / 'cancelled' / 'unavailable'
 *
 * 归类口径（依据 DSH 实际触发面：只有沙箱升权重试会触发审批）：
 *   - bash / pwsh 工具 → 执行命令（覆盖「删文件 / 装包 / 改系统」）
 *   - write / edit 工具 → 写文件到工作区外
 *   - 网络访问不触发审批（DSH 无直接出站 HTTP 工具通道），故无对应归类
 *
 * 出站 HTTP 用 Node 内置 fetch（headless profile 是无 shell 的极简模式，不依赖 shell/curl）。
 */
export const name = 'xiao-approval-bridge'

const BUCKET_OF_TOOL = {
  bash: 'command',
  pwsh: 'command',
  write: 'write_outside',
  edit: 'write_outside',
}

// 工作面板：从工具入参里提取一句话摘要（命令 / 文件 / 查询词……）
function summarizeArgs(toolName, args) {
  const a = args && typeof args === 'object' ? args : {}
  let s = ''
  if (toolName === 'bash' || toolName === 'pwsh') s = a.command || a.script || a.path || ''
  else if (toolName === 'write' || toolName === 'edit' || toolName === 'fs') s = a.file_path || a.path || a.file || ''
  else if (toolName === 'web' || toolName === 'web_search' || toolName === 'search') s = a.query || a.prompt || ''
  else if (toolName === 'skill') s = a.name || ''
  else if (toolName === 'subagent' || toolName === 'subagent_fork') s = a.description || a.prompt || ''
  else if (toolName === 'workflow') s = a.args && a.args.name ? a.args.name : ''
  else if (toolName === 'todo' || toolName === 'todo_write') s = (a.todos || []).map((t) => t.content).filter(Boolean).join('、')
  else {
    try {
      s = JSON.stringify(a)
    } catch (e) {
      s = ''
    }
  }
  if (!s) return ''
  s = String(s).replace(/\s+/g, ' ').trim()
  return s.length > 90 ? s.slice(0, 90) + '…' : s
}

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function apply(ctx) {
  const XIAO = 'http://127.0.0.1:8123/api/dsh/approval'
  const STEP = 'http://127.0.0.1:8123/api/dsh/step'

  // 预授权清单：小二后端启动 DSH 时经 XIAO_GRANT 传入（如 ["network","install"]）
  let grant = new Set()
  try {
    const raw = process.env.XIAO_GRANT
    if (raw) grant = new Set(JSON.parse(raw))
  } catch (e) {
    console.error('[xiao-approval] XIAO_GRANT 解析失败：', e && e.message ? e.message : e)
  }

  // 实时工作面板：每个工具执行完就上报一步，前端 live 展示（上报失败不影响主流程）
  ctx.on('tools/result', (exec, result) => {
    try {
      const name = exec && (exec.name || exec.toolName) || 'tool'
      const summary = summarizeArgs(name, exec && exec.arguments)
      const status = result && result.isError === true ? 'error' : 'done'
      post(STEP, { name, status, summary }).catch(() => {})
    } catch (e) {
      /* 完全静默 */
    }
  })

  ctx.on('approval/request', async (req) => {
    if (req && req.signal && req.signal.aborted === true) return 'cancelled'
    const toolName = req && typeof req.toolName === 'string' ? req.toolName : ''
    const reason = req && typeof req.reason === 'string' ? req.reason : ''
    const bucket = BUCKET_OF_TOOL[toolName] || null

    // 预授权命中 → 自动放行（不打扰语音）
    if (bucket === 'command' && (grant.has('delete') || grant.has('install') || grant.has('system'))) {
      return 'allowed-once'
    }
    if (bucket === 'write_outside' && grant.has('write_outside')) {
      return 'allowed-once'
    }

    // 否则走语音确认。把 reason 的 "escalate sandbox to X: " 前缀剥掉，只留模型的一句话理由
    const label = bucket === 'command'
      ? '执行命令'
      : bucket === 'write_outside'
        ? '写文件到工作区外'
        : toolName || '该操作'
    const why = reason.replace(/^escalate sandbox to [^:]+:\s*/i, '')
    const action = why ? `${label}：${why}` : label

    try {
      const res = await fetch(XIAO, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
        signal: AbortSignal.timeout(45000),
      })
      const data = await res.json()
      const decision = data && data.decision
      if (decision === 'allowed-once' || decision === 'rejected') {
        return decision
      }
      console.error('[xiao-approval] 小二返回异常：', JSON.stringify(data))
      return 'unavailable'
    } catch (e) {
      console.error('[xiao-approval] bridge error：', e && e.message ? e.message : e)
      return 'unavailable'
    }
  })
}
