/**
 * ⚠ 已废弃（deprecated）：仅作 cordis_define 函数体写法历史参考。
 * 正式实现以 plugins/xiao-approval-bridge/lib/index.js（持久化版）为准，
 * 后者含 XIAO_GRANT 预授权 + tools/result 实时上报，逻辑更新更全。
 * 本文件不再维护，请勿以其为准。
 *
 * 小二（Xiao）语音审批桥 —— DSH 薄插件（host 侧）
 *
 * 作用：挂在 DSH 的 `approval/request` 瀑布上，作为 answerer 把审批请求
 * 转发给小二后端（Python，127.0.0.1:8123）做语音确认，再把决定注回瀑布。
 *
 * 结果词表与 DSH approval 服务一致：
 *   'allowed-once'（唯一放行）/ 'rejected' / 'cancelled' / 'unavailable'
 *
 * 依赖（真实 DSH 接口，已按 @deepseek-ai/dsh-tool-cordis 的 typet 定义核对）：
 *   - 事件 `approval/request`：waterfall，(req: ApprovalRequest, next) => Promise<ApprovalOutcome>
 *     ApprovalRequest = { agent, toolName, callId?, reason?, signal? }
 *   - 服务 `shell`：resolve(req: ShellExecRequest) => ShellExecSpec；run(spec) => ShellRunResult
 *     ShellRunResult.stdout.text 为字符串输出
 *
 * 说明：host 侧动态插件没有 fetch，出站 HTTP 用 shell + curl.exe + stdin 传 JSON 完成，
 * 避免 JSON 引号在 cmd/pwsh/bash 之间的转义问题。curl.exe 已在本机确认存在。
 *
 * 这是「函数体」写法（供 cordis_define 的 code.host 用）；持久化安装时按
 * build-deepseek-harness-plugin 打包成可安装组合包。
 */
return {
  apply(ctx) {
    const XIAO = 'http://127.0.0.1:8123/api/dsh/approval'

    ctx.on('approval/request', async (req, next) => {
      try {
        const shell = ctx.get('shell')
        if (shell === undefined) {
          console.error('[xiao-approval] shell 服务不可用，失败关闭')
          return 'unavailable'
        }
        // 只取审批请求的叶子字段，拼成一句人话给小二播报
        const parts = []
        if (req && typeof req.toolName === 'string' && req.toolName) parts.push(req.toolName)
        if (req && typeof req.reason === 'string' && req.reason) parts.push(req.reason)
        const action = parts.join('：') || '该操作'

        const spec = shell.resolve({
          command: `curl.exe -s -X POST ${XIAO} -H "Content-Type: application/json" --data-binary @-`,
          stdin: JSON.stringify({ action }),
          timeoutMs: 45000,
        })
        const result = await shell.run(spec)
        const raw = (result && result.stdout && result.stdout.text) || ''

        let decision = null
        try {
          decision = JSON.parse(raw).decision
        } catch (e) {
          decision = null
        }
        if (decision === 'allowed-once' || decision === 'rejected') {
          return decision
        }
        console.error('[xiao-approval] 小二返回异常：', raw)
        return 'unavailable'
      } catch (e) {
        console.error('[xiao-approval] bridge error:', e)
        return 'unavailable'
      }
    })
  },
}
