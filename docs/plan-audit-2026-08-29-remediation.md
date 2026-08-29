# 审计 2026-08-29 修复方案计划

- 依据：docs/archive/audit-2026-08-29.md（Xiao 项目深度审计，综合 69/100）
- 范围：backend 全部、frontend/src、desktop、plugins/xiao-approval-bridge、tests、scripts
- 约束：不触碰 M0-a1（backend/gateway/ 出网网关配置 schema 模块）与 _M0-tasks/ 任何文件
- 状态：方案 = 规划稿，未落地。逐项实现前需按「确认驱动」口径与业务规则对齐。

---

## 0. M0-a1 边界与「零影响」保证

M0-a1（_M0-tasks/A1-config-schema.md）定义为出网安全网关的配置 schema，交付物为：

- backend/gateway/compliance.yaml（契约字段：local_only_keywords / obfuscation_mapping / suggested_entities_max / debug_log / enabled）
- backend/gateway/load_config.py（load_compliance(path=None) -> dict）
- A1 硬约束：不修改 backend/config.py，网关配置与主配置独立、解耦。

已核实：backend/gateway/ 全目录未出现 backend.config / config. 的任何引用（grep 为空）。网关链路只读自己的 compliance.yaml，与主 config.py 无运行期耦合。

因此本方案满足「不影响 M0-a1」的保证，落地时强制两条红线：

1. 不改 backend/gateway/*、不改 _M0-tasks/*。
2. 所有对 backend/config.py 的改动保持对外行为不变（只增校验与原子性），不得新增 gateway 与 config 的耦合。

---

## 1. 严重问题（C1–C6）修改方案

### C1 · open_app.py 越权执行（LLM→进程） ★ 高危

- 现状（backend/tools/open_app.py）：os.path.exists(t) → os.startfile(t)（任意路径）；否则 subprocess.Popen([t])（裸拉起任意可执行名）。聊天通道无审批门禁。
- 方案：
  1. 应用名白名单（notepad/calc/browser 等），仅白名单应用可裸 Popen，其余拒绝。
  2. URL 仅放行 http(s)（webbrowser.open），其余协议/裸 Popen 一律拒绝。
  3. 审批钩子：复用既有 Pipeline.request_tool_approval(action, prompt=None) -> bool（core.py:625，computer 工具已在用），不做 approvals.request 等未存在接口。文件打开/应用启动先人审，allowed-once 才执行。
  4. 线程语义（重要）：OpenAppTool.run() 已是 async（内部 await asyncio.to_thread(_open)），因此「先 await 审批、后 to_thread 执行」——审批 await 必须在事件循环侧 run() 里完成，不能放进 _open() 同步线程，否则跨线程调用审批会出错。
  5. 仿 computer.py 的 fail-closed：确认钩子缺失或审批异常一律拒绝执行；可引入 tools.open_app.confirm 分类粒度（打开网址/文件/应用），默认从严。
  6. 低风险「打开网址」可视口径放行；「启动应用/打开文件」一律审批。
  4. 低风险「打开网址」可视口径放行；「启动应用/打开文件」一律审批。
- 落地依赖：给 OpenAppTool 增加注入审批回调（仿 computer.py 的 set_confirm_hook），在 startup() 里 open_app.set_confirm_hook(pipeline.request_tool_approval)。
- 对 M0-a1 影响：无。
- 验收：白名单外应用被拒；https:// 正常打开；文件路径打开需语音/按钮审批；提示注入无法拉起 cmd.exe 等。

### C2 · /api/config 任意写入 + 非原子落盘（提权链） ★ 高危

- 现状：main.py:180-189 update_config 直接 config.update(updates); config.save()，无校验/白名单；config.py:57-65 update() 深合并、save() 直接 open 非原子（断电留半截 YAML）。
- 修正审计示例（重要）：审计建议在 config.update() 内置 _PROTECTED={perms,approval,tools} 并 raise PermissionError。该做法会误伤本项目合法设置页：backend/settings_schema.py:261,264-268 明确把 tools.computer.enabled、approval.enabled/timeout_ms/allow_phrases/deny_phrases、perms.standing_grants 作为可编辑字段（settings 前端经 /api/config 落盘）。把这几段写死，设置页将无法保存（含审批开关、常驻授权下拉）；且 perms.set_granted()（backend/perms.py:47-55）本身经 config.update()+save() 合法改写 perms.standing_grants，保护段会打断它。
- 修正后方案（保护放 API 边界，而非 Config.update 内部）：
  1. 写校验（防「任意键注入」）：update_config 按 settings_schema.SCHEMA 校验路径 + 值类型/枚举，拒绝未声明路径或非法值。
  2. 高权段排除（防「合法字段提权/静默改写」，关键）：perms.*、approval.*、tools.* 虽在 schema 白名单内（可渲染），/api/config 仍应**拒写**——这些段只能走专用端点（/api/perms/standing、/api/perms/deferred、审批流）。已核实 perms.standing_grants 的权威写路径是 main.py:463-473 set_standing → perms.set_granted()，而非 /api/config。Config.update 保持无保护（set_granted 合法调用不被误伤）。
  3. 前端协同（不可省略）：settings_schema.py:261,264-268 把这三段列为「可由设置页编辑并回填 /api/config」的字段；一旦 /api/config 拒写，前端需把这三段改走对应专用端点，否则设置页的审批开关/常驻授权/语音操电脑开关保存会失效。此条为「后端+前端」协同取舍，须排入改动范围，不能只改后端。
  4. 原子落盘：Config.save() 改为同目录临时文件 + flush + os.fsync + os.replace。仅提升断电/崩溃安全，不改行为，对 M0-a1 透明。
- 对 M0-a1 影响：无。
- 验收：设置页 perms/approval/tools 字段仍可正常保存（经专用端点）；/api/config 拒写未声明路径及 perms/approval/tools 段；config.save() 不再产生半截 YAML。

### C3 · DNS Rebinding（loopback_guard 仅查 client.host） ★ 高危

- 现状：main.py:40-51 loopback_guard 只比对 request.client.host；ws_endpoint（main.py:120）、dsh_approval（415）、dsh_step（437）同理。恶意域名解析到 127.0.0.1 即绕过。
- 方案：在 loopback_guard 及 WS 入口追加 Host 头校验：
  host = (request.headers.get(host) or "").split(":")[0].lower().strip("[]")
  若 host 不在 {127.0.0.1, localhost, ::1} → 返回 403 非法 Host 头。
  ws_endpoint 在 accept() 前同样校验 websocket.headers 的 host。
- 对 M0-a1 影响：无。说明：A1 阶段网关仅是配置加载模块（compliance.yaml + load_config.py），非 127.0.0.1 服务；M0.2 网关为未来本地服务，与本次 HTTP Host 校验无冲突。
- 验收：curl -H Host:evil.com http://127.0.0.1:8123/api/config → 403；正常 localhost 不受影响。

### C4 · omni/wake 音频线程无超时（唤醒/识别线程冻结） ★ 高危

- 现状：backend/asr/omni.py:20 OpenAI(...) 无 timeout；stop()（:35）内同步 chat.completions.create(...) 阻塞。wake_omni.py:26-28 在音频线程内调用 start/feed/stop，omni 服务一挂唤醒线程永久阻塞。
- 方案（修正：「走保底引擎」不成立）：已核实 build_asr()/wake_omni 均为**单引擎选择，运行时无跨引擎切换**，因此不能声称「切到本地 ASR 接管」。改为：客户端级超时（OpenAI(..., timeout=5.0, max_retries=1)），并在 wake_omni.feed()/OmniASREngine.stop() 用 try/except 兜底，超时/异常即**放弃本次唤醒/识别并记日志**，保证音频主循环不崩、不永久阻塞。若要真正按需切换本地 ASR，需另实现 fallback 链（超出本审计「约 10 行」），另立项。
- 对 M0-a1 影响：无。
- 验收：停掉 vLLM-omni 服务后，唤醒/识别线程数秒内返回/不崩，日志有超时记录，主循环不永久阻塞。

### C5 · TTS 合成无超时（speak() 永不返回） ★ 高危

- 现状：backend/tts/edge_tts.py:92 async for chunk in communicate.stream() 无超时；backend/tts/omni.py:77-78 OpenAI(...) 无 timeout，任一云引擎挂起会堵死整条对话流水线。
- 方案（修正：「降级链」不成立）：已核实 build_tts() 只按 tts.active 选**单一引擎**，pipeline 持单一 tts 实例，**运行时无跨引擎回退链**——「超时→降级 Piper」并非现状能力。改为：edge_tts._synthesize 用 asyncio.wait_for(..., timeout=10.0) 包裹；tts/omni 加客户端 timeout + 合成侧 wait_for 兜底；超时抛错被 speak()（edge_tts.py:70）的 try/except 捕获 → 记日志 + 返回占位，**主对话流水线不阻塞**（这才是 C5 要防的「堵死」）。若坚持「真回退链」，须新增 pipeline speak() 的多引擎 fallback（云→本地），改动进入 core.py speak 调度、面大于 10 行，建议阶段一不做、阶段二单独立项。
- 对 M0-a1 影响：无（TTS 层）。
- 验收：断网/宕机场景 speak() 在超时内返回错误（不阻塞主流程），日志有超时记录；主对话不被云引擎挂起拖死。

### C6 · 审批桥 command 预授权过宽 ★ 高危（需核实）

- 现状核实（plugins/xiao-approval-bridge/lib/index.js:88-96）：command 放行要求 grant 含 delete/install/system 之一；未匹配工具（bucket=null）已落入语音确认而非自动放行。与审计描述的无条件通配有出入。
- 残余真实风险：用户预授权 install（或 delete/system）任一分类，就自动放行全部 bash/pwsh 命令，分类被过度合流。「装包」常驻授权即等于「任意命令免审」。
- 方案：
  1. 收窄：删除任一分类 grant → 放行所有 command 的宽匹配；改为 install 类授权仅放行明确的 pip/npm 安装类命令（按正则识别），其余命令一律人审。
  2. 删除任何通配兜底，未知动词/未知工具一律 needs_approval（宁严勿松）；当前 bucket=null 已满足，保留并补回归测试。
  3. 与 perms.py 的 CATEGORIES 口径对齐，避免预授权清单与分类判定两套逻辑漂移。
- 对 M0-a1 影响：无（DSH host-only 插件）。
- 验收：仅授权 install 时，pip install xxx 自动放行、rm -rf / bash <任意> 必须审；无任何未审命令绕过。

---

## 2. 优化建议（O1–O12）

优先级：O9/O10/O11（安全/运维）、O8（隐私）、O7（用户感知）、其余按需。部分条目已核实可能被后续提交修复，实现前先复核。

| # | 位置（审计引用） | 建议 | 备注 |
|---|---|---|---|
| O1 | main.py:163 WS 断连 sender 残留 | finally 中 senders.discard(ws) | 已核实基本已修复：当前 ws_endpoint 用 bus.subscribe(_push) + finally: unsub()（session/state.py:32-41 的 _unsub 会移除订阅）。仅需确认无其它残留注册路径；实现时复核。 |
| O2 | tasks.py:97-98 + core.py:745-748 TaskManager 跨线程无锁 | 加 threading.Lock 或统一 loop.call_soon_threadsafe | 已核实风险低：tasks.py:37-56 submit() 在事件循环内创建 asyncio.Task，core.py 音频线程已用 run_coroutine_threadsafe/call_soon_threadsafe 汇入事件循环，_tasks/_order 实际单线程。若无跨线程调用则标记无需改。 |
| O3 | cloud_paraformer 设全局 dashscope key | 用请求级凭据传参 | 待核实 cloud_paraformer.py:47-48 上下文。 |
| O4 | qwen_realtime.py:256-260 实例状态无互斥 | asyncio.Lock 保护 connecting/ready 迁移 | 待核实。 |
| O5 | piper.py:52 mixer 按 22050 初始化 vs piper 输出 24000 | 采样率从 WAV 头动态读取 | 待核实，纯音质优化。 |
| O6 | cloud_paraformer.py:47-48 on_error: pass | 记 logger.warning + 回传占位结果 | 待核实。 |
| O7 | asr/factory.py:70-72 初始化失败静默回退 | 回退时发 WS 通知 | 待核实，用户感知改进。 |
| O8 | router.py:52-58 语音转写明文落盘 | 确认 logs/ 已 gitignore + 提供不留存开关 | 已具备 gitignore；补开关。 |
| O9 | desktop/main.js:113-117,150 端口被占后「不校验身份即放行」 | 握手 token：后端启动生成随机 token，前端加载前校验 | 已核实 ensureBackend（:113-117）在端口被占时仅 return 不启动、也不校验端口上进程身份，webview 仍会连到 127.0.0.1:8123 上的占位进程 → 风险成立；安全改进，建议优先。 |
| O10 | plugins 桥 ↔ 后端无共享凭证 | 本地 socket 加随机 token | 安全改进，建议优先。 |
| O11 | desktop/package.json Electron 31 已 EOL | 升至当前稳定大版本 | 升级类操作需单独排期 + 回归（涉及打包产物）。 |
| O12 | 测试：音频/asr/tts 17 文件 + main.py 21 端点零测试；test_tasks.py:87-88 sleep 等异步；fakes 复制粘贴；_play_blocking ×4；引擎默认值 ×3 重复 | 优先补 open_app/config/perms 三条安全路径测试；sleep 改事件驱动等待；fakes 抽公共包；默认值收敛 config.yaml | 见 §3。 |

---

## 3. 测试补充（优先安全路径）

1. C1 open_app 安全路径：白名单外应用拒绝；URL 仅 http(s)；文件/应用打开需审批；审批拒绝返回用户拒绝该操作。
2. C2 config 写入：任意未声明路径 POST /api/config 被拒；设置页 perms/approval/tools 字段仍可写；save() 原子性（模拟中断无半截文件）。
3. C3 Host 头：Host:evil.com 非法请求 → 403；正常 localhost 放行。
4. C4/C5 超时与降级：mock 挂起的 omni/edge-tts，断言超时内抛错并走保底引擎。
5. C6 审批桥：仅 install 预授权时，安装类命令放行、rm -rf/任意命令必须人审；未知动词走审批。
6. 现有测试工程化（O12）：test_tasks.py sleep 改事件驱动；fakes 抽公共包；引擎默认值单源化。

> 测试选择「拒绝优先」口径（approval 词表：允许/拒绝/超时→拒绝），与 core.py:574-588 _approval_decision 一致。

---

## 4. 实施顺序与工作量（预测）

| 序 | 项 | 优先级 | 估算 |
|---|---|---|---|
| 1 | C2（API 写入校验 + 原子落盘） | 提权链，收益最大 | ~0.5 天 |
| 2 | C6（审批桥收窄 command 放行 + 回归测试） | 提权链，改动小 | ~0.5 天 |
| 3 | C1（open_app 白名单 + 复用审批钩子） | 高危 | ~0.5 天 |
| 4 | C3 / C4 / C5（Host 校验 + 三处超时） | 各约 10 行 | ~0.5 天 |
| 5 | C1–C6 安全测试 | 交付门槛 | ~1 天 |
| 6 | O9 / O10 / O7 / O8 | 安全/用户体验 | 另排期 |
| 7 | O3/O6/O4/O5、O12 工程化 | 技术债 | 另排期 |
| 8 | O11（Electron 升级） | 运维/安全 | 单独排期 + 打包回归 |

修完 C1–C6，综合分（69）预计可从 69 → 80+，达到「第三使用者、陌生环境开箱即用」标准（对照 AGENTS.md 项目规则）。

---

## 5. 对审计示例的修正意见（重要，供评审）

| 审计示例 | 问题 | 修正 |
|---|---|---|
| C2 _PROTECTED={perms,approval,tools} 放 Config.update | 会误伤设置页（settings_schema.py:261,264-268 将三者作为可编辑字段）与 perms.set_granted() 合法路径 | 把保护移到 API 边界（update_config 按 settings_schema 白名单校验 + 高权字段收敛到专用端点）；Config.update 保持无保护以保内部合法调用；原子落盘保留。 |
| C6 「命令 bucket 通配兜底」 | 与现状不符：当前 command 放行需显式 grant，未匹配工具已走语音确认 | 现状已非通配；改为收窄单授权→全命令放行的过度合流（install 仅放行安装类命令，其余人审），并补回归测试。 |
| C1 await approvals.request(...) | approvals 接口在代码中不存在 | 复用既有 Pipeline.request_tool_approval（core.py:625），与 computer 工具同路径。 |

---

## 6. 交付与后续

- 本计划不含任何代码改动，仅规划稿。落地前请按项目「确认驱动」流程逐项确认业务规则/口径。
- 落地代码变更清单建议按 C1→C6 拆分提交，每个提交附带对应测试；全程不改 backend/gateway/* 与 _M0-tasks/*。
- 如需我基于本计划生成实际补丁或逐个任务的实现，请确认。

---

## 7. 再检视修订说明（2026-08-29 复核版）

在原始规划上做了一次代码级复核，修正如下（均不影响 M0-a1）：

- R1 · C5「超时→降级 Piper」**不成立**：build_tts() 为单引擎选择（tts.active 只挑一个），pipeline 持单一 tts，运行时无跨引擎回退链。已改为「超时→报错不阻塞主流水线（先保不堵死）」；真回退链需另行立项（进入 core.py speak 调度）。
- R2 · C4「切本地 ASR 接管」**不成立**：build_asr/wake_omni 同样单引擎、无运行时切换。已改为「超时/异常→放弃本次唤醒识别 + 记录日志，主循环不崩」。
- R3 · C2 补「高权段排除 + 前端协同」：perms/approval/tools 在 schema 白名单内（可渲染），故仅「按 schema 校验」不足以防提权；/api/config 需拒写这三段（权威写路径是 /api/perms/standing，main.py:463-473），且需前端把这三段改走专用端点（否则设置页对应字段保存失效）。= 后端+前端协同取舍。
- R4 · C1 补充线程语义：审批 await 须在 run()（事件循环侧）完成，不放进 _open()（同步线程）。
- R5 · C3 措辞：A1 阶段网关仅为配置加载模块（非 127.0.0.1 服务），已修正表述。
- R6 · O9 现状复核：ensureBackend 端口被占仅 return 不校验身份，webview 仍连占位端口 → O9 风险成立，已更新。

M0-a1 合规：以上修订均未触碰 backend/gateway/* 与 _M0-tasks/*；C2 高权段排除不涉及网关 compliance 配置（其由 load_config.py 独立加载，不经 /api/config）。
