# 小二（Xiao）· 前端交互优化清单（打包前）

> 版本 v1.0 ｜ 2026-09-01 ｜ 两份走查合并（主界面 + 设置面板/样式），共 38 项
> 三档：🔴 必须修（高）/ 🟡 建议修（中）/ ⚪ 可选（低）

## 一、性能（根组件高频重渲染）

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 1 | 🔴 | 顶栏时钟每秒 `setClock` 触发 App 根组件全量重渲染（波及 messages/Nebula/所有面板） | App.tsx:231-241 |
| 2 | 🔴 | `mic_level`(约10/s)、`asr_partial` 高频事件直接 setState 全量重渲染 | App.tsx:303-311 |
| 3 | 🟡 | `setPath` 每次 JSON 全量深拷贝，applyModel/applyTTS 一次触发 10+ 次 setField | SettingsPanel.tsx:187-197/547-576 |
| 4 | 🟡 | assistant 消息 `Typewriter` 25ms 逐字 setState，长文本持续数秒高频重渲染 | Typewriter.tsx:6-16 |

## 二、稳定性 / 正确性

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 5 | 🔴 | `pickImages` 的 `Promise.all` 无 `.catch`（未捕获 rejection）；超限/非图片文件静默丢弃无提示 | App.tsx:502-517 |
| 6 | 🔴 | 危险操作（删除模型/方案）无二次确认；`clearMemory` 无 busy 防重 | SettingsPanel.tsx:657/909/1104/377 |
| 7 | 🟡 | `tool_result`/`work_step` 用 `name` 反向匹配，同名工具并发时挂错条目 | App.tsx:339-348/355-366 |
| 8 | 🟡 | PermsPanel checkbox 无 busy/非乐观更新，快速连点竞态 | PermsPanel.tsx:40-70 |
| 9 | 🟡 | 删除当前激活项后 `llm.cloud.*` 等旧值残留，依赖后端 fallback | SettingsPanel.tsx:657-664 |
| 10 | 🟡 | number 输入 `Number('')=0` 无法清空；topP/topK 无范围校验 | SettingsPanel.tsx:470/764/768 |

## 三、交互反馈

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 11 | 🟡 | 发送无连接保护/loading：WS 未连时 send 入队失败丢最旧，前端却已显示消息 | App.tsx:544-552 |
| 12 | 🟡 | 路由切换乐观更新无回滚，断线时状态漂移 | App.tsx:569-572 |
| 13 | 🟡 | 截屏失败/拒绝静默 return，无提示无 loading，可连点 | App.tsx:519-542 |
| 14 | 🟡 | WS 断线/重连无用户可读提示、无失败原因 | ws.ts:63-73 |
| 15 | 🟡 | 回顾面板 `recallData===null` 与空态混淆，拉取失败也显示「还没有记录」 | App.tsx:435-445/884-910 |
| 16 | 🟡 | 设置面板无未保存更改保护，关闭直接丢改动 | SettingsPanel.tsx:295-326 |
| 17 | ⚪ | `previewBusy` 全局锁过宽，多方案试听互相阻塞 | SettingsPanel.tsx:328/346 |
| 18 | ⚪ | `runProbe` 无 AbortController/超时，后端卡死前端一直转 | SettingsPanel.tsx:264-283 |

## 四、无障碍

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 19 | 🔴 | 模态框（主面板+LLM/ASR/TTS/唤醒 4 子模态）无 `role=dialog`/`aria-modal`、无焦点陷阱、无 Esc 关闭 | SettingsPanel.tsx:672/924/1119/1402 |
| 20 | 🟡 | `scheme-item-main` 用 div 承载 onClick，不可键盘操作 | SettingsPanel.tsx:814/1007/1236 |
| 21 | ⚪ | `focus-visible` 不统一（.btn 有，nav-item/chip/modal-close 缺） | styles.css:551-555 |
| 22 | ⚪ | 唤醒/打断/审批按钮无按状态禁用，可连点 | App.tsx:554-556/267-269 |

## 五、视觉 / 样式

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 23 | 🔴 | 颜色 token 未落地，硬编码色值散落 20+ 处；`--danger` 未在 :root 定义靠兜底 | styles.css 多处 |
| 24 | ✅ | CosyVoice v3 / Qwen-Audio-TTS 前后端均已清理（前端 SettingsPanel 音色列表 + 后端 cosyvoice.py/factory/settings_schema/provider_test 分支已删） | 已清理 |
| 25 | ⚪ | 样式重复/覆盖式声明 5 处 | styles.css:470vs1448 等 |
| 26 | ⚪ | BEM 命名混用 | styles.css:1261/1342 |
| 27 | ⚪ | 设置面板窄屏无响应式（nav 固定 196px，actions 不换行） | styles.css:717-721/728 |
| 28 | ⚪ | 10-11px 小字 + opacity .55-.75 对比度可能低于 AA 4.5:1（需实测） | styles.css:903/877/1047 |

## 六、代码质量 / 杂项

| # | 档 | 问题 | 位置 |
|---|---|---|---|
| 29 | 🟡 | `show_if` 显隐逻辑双维护（自定义 renderXXX 前缀过滤 vs `visible()` 仅服务默认分支），且 `===` 严格相等 + value 定死 string | SettingsPanel.tsx:1631-1655/410-413 |
| 30 | 🟡 | `messages` 无上限累积（logs/workSteps 都有 slice(-199)），长期运行线性增长 | App.tsx:250-254 |
| 31 | ⚪ | 向导语言选择 en 是「即将支持」假选项，选中不生效、不落盘 | OnboardingWizard.tsx:52-55/237-243 |
| 32 | ⚪ | TaskPanel 打开不主动拉取任务、无刷新按钮 | TaskPanel.tsx:28 |
| 33 | ⚪ | `lifeOf` 在 VoiceLine 与 Nebula 重复实现 | VoiceLine.tsx:5-30/Nebula.tsx:280-305 |
| 34 | ⚪ | ws 收到无法解析消息静默 catch 忽略 | ws.ts:81-82 |
| 35 | ⚪ | interruptTimer 卸载未清理 | App.tsx:244/300-301 |
| 36 | ⚪ | 清空日志/工作台无二次确认 | App.tsx:769/WorkPanel.tsx:45 |
| 37 | ⚪ | PermsPanel 首次加载无 loading 态 | PermsPanel.tsx:83-96 |
| 38 | ⚪ | 审批/唤醒等按钮无按压态反馈 | App.tsx |

## 建议实施顺序

- **批 1（核心，约 22 项）**：§一 性能（1-4）+ §二 稳定性（5-10）+ §三 交互反馈（11-16）+ §四 无障碍（19-20）
- **批 2（打磨，约 16 项）**：§五 视觉/样式（23-28）+ §六 代码质量（29-38）

> 需老板拍板的方向：§六-31 语言 en 选项（禁用 vs 落地 i18n）。
