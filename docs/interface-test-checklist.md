# 小二（Xiao）· 界面手动走查清单（打包前）

> 版本 v1.0 ｜ 2026-08-31 ｜ 收口任务 P0（界面测试）
> 走查对象：①首启流程 ②存储满弹窗 ③成长双轨回顾面板
> 走查方法：无人值守环境下以「静态走读（代码级逐项核对交互闭环）+ 后端 API 冒烟」替代人工点击，本清单作为人工复验依据交付。

## 结论概览

| 对象 | 交互闭环 | 结论 |
|---|---|---|
| 首启流程 OnboardingWizard | 6 步 + 跳过 + 保存 + 后端未启动兜底 | ✅ 通过（1 项低优先级观察 F3） |
| 存储满弹窗 | storage_threshold 事件 → 三按钮 → 后端 storage_action | ✅ 通过 |
| 成长双轨回顾面板 | 回顾按钮 → /api/recall → 三栏 + 空态 | ✅ 通过 |
| 打包前跨层核验 | models / 授权 / 路径 | ⚠️ 2 项阻塞（F1/F2）+ 1 项建议（F4） |

## 一、首启流程（`frontend/src/components/OnboardingWizard.tsx`）

触发：`App.tsx` L195 `showWizard = !localStorage.getItem('xiao_onboarded')`；完成后写 marker 不再弹。

| # | 步骤 | 交互点 | 实现位置 | 走读结论 |
|---|---|---|---|---|
| 0 | 欢迎 | 界面语言选择 zh/en | L228-246 | ⚠️ F3：lang 选中后 `finish()` 未落盘，config 无 lang 字段（en 已标「即将支持」，属占位） |
| 1 | 领 Key | DeepSeek/通义千问卡片 + 领 Key 步骤 + 官方链接 | L248-269 | ✅ 链接 `target=_blank rel=noreferrer` |
| 2 | 连通测试 | 服务商/模型名/Key + 「测试连通」 | L271-314 | ✅ `canNext = testOk===true` 强制测试通过才能下一步；未填 Key 有「跳过」兜底 |
| 3 | 选大脑 | 路由 3 选项 + DSH 检测 | L316-334 | ✅ `/api/status` 探测 dsh_available |
| 4 | 测麦克风 | 「录一句试试」回声测试 | L336-349 | ✅ `/api/mic/echo`，低音量/失败有区分文案 |
| 5 | 完成 | 保存并完成 | L351-361 | ✅ `/api/config` POST，cfg 为 null 时拦截并提示 |
| 任意 | 跳过 | 写 marker 关闭向导 | L148-151 | ✅ 全流程可跳过，开箱可进 L0 |

人工复验点：在无 Key 场景点「跳过」应能进主界面；填错 Key 点「测试连通」应出现 ❌ 文案且「下一步」置灰。

## 二、存储满弹窗（`frontend/src/App.tsx` L832-854）

| # | 项 | 实现位置 | 走读结论 |
|---|---|---|---|
| 1 | 触发 | `storage_threshold` 事件（后端 `backend/memv1/maintenance.py` sweep 检测 80%/95%） | ✅ 事件驱动，去重后仅阈值变化时弹 |
| 2 | 文案 | used_mb/budget_mb + level warn(80%)/critical(95%) 区分 | ✅ L840-842 |
| 3 | 暂不处理 | 发 `storage_action ignore` + 关弹窗 | ✅ |
| 4 | 提升空间 | 关弹窗 + 打开设置面板 | ✅ |
| 5 | 清理旧记忆 | 发 `storage_action clean` → 后端 `clean_now()`（P0 永不失效） | ✅ `backend/main.py` L204-214 |

人工复验点：调低 `memory.storage_budget_mb` 触发阈值后应弹窗；点「清理旧记忆」后应收到 `storage_cleaned` 提示且共同记忆/成长记录不受影响。

## 三、成长双轨回顾面板（`frontend/src/App.tsx` L874-928）

| # | 项 | 实现位置 | 走读结论 |
|---|---|---|---|
| 1 | 入口 | 顶栏「回顾」按钮 `openRecall` | ✅ L613 |
| 2 | 拉取 | `GET /api/recall`（`RecallComposer(GrowthStore()).compose()`） | ✅ `backend/main.py` L466-475 |
| 3 | 三栏 | 你的成长（milestone）/ 小二的成长（milestone）/ 咱们的回忆（event） | ✅ 字段与 `backend/m6/recall.py` 白名单一致 |
| 4 | 空态 | 各栏「还没有记录」 | ✅ |
| 5 | 呈现纪律 | 只读翻看、时间倒序、无奖杯弹窗 | ✅ 符合 M6-growth.md §4.1 |

人工复验点：有成长记录时三栏分列显示、时间倒序；无记录时三栏均空态。

## 四、打包前跨层核验发现（问题清单）

| 编号 | 级别 | 问题 | 证据 | 处置 |
|---|---|---|---|---|
| F1 | ✅ 已解决 | `models/` 内 Piper 声库曾为 **huayan**（Unknown 授权）残留，非 chaowen(CC0) | 已换 chaowen(CC0) 就位，huayan 移入 `_archive/` | ✅ chaowen 就位 + huayan 归档 |
| F2 | 🔴 阻塞 | sherpa 唤醒模型目录缺 **Apache-2.0 LICENSE/NOTICE**，违反「分发包内保留其声明」口径 | `models/sherpa-onnx-kws-.../` 仅 README.md，无 LICENSE | 补 LICENSE/NOTICE |
| F3 | 🟡 低 | 首启向导语言选择不落盘（config 无 lang 字段） | OnboardingWizard L236 选择后 finish() 未保存 | 记录，不阻塞（en 占位） |
| F4 | 🟡 中 | `config.yaml` `agent.workspace: ../03_Workspace` 为开发者本机三段式命名痕迹，随包分发后第三方环境无此目录 | config.yaml L170 | 建议改通用默认值，待老板拍板 |

> F1/F2 为分发前必须清零的阻塞项，已纳入打包前清理步骤。
