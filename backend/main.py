"""FastAPI 入口：WebSocket 事件中继、手动唤醒/文本输入、健康检查、静态托管。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend.agent import Agent
from backend.audit import build_auditor
from backend.authorization import AUTHORIZATION_ITEMS, AuthorizationCenter
from backend.bridge import DSHBridge, build_bridge
from backend.config import ROOT, config
from backend.core import Pipeline
from backend.llm.factory import build_llm
from backend.errors import human_reason
from backend.perms import CATEGORIES, Perms
from backend.provider_test import test_provider
from backend.router import Router
from backend.session.state import State, bus, emit
from backend.tasks import TaskManager
from backend.tools import computer, open_app, register_builtin_tools
from backend.tools.base import registry
from backend.tts.factory import build_preview_tts, build_tts
from backend.config_guard import validate_config_updates

app = FastAPI(title="Xiao Voice Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def loopback_guard(request: Request, call_next):
    """统一回环校验：所有 /api/* 端点仅接受本机来源（🔒 安全红线，永不外放）。

    覆盖 config / memory / tasks / perms / dsh 等全部 REST 端点，一处兜底，
    避免逐端点遗漏（保留 dsh_approval / dsh_step 的内联校验作双保险）。
    /health 天然走回环；/ws 在端点内另有显式回环校验。
    """
    if request.url.path.startswith("/api/"):
        host = request.client.host if request.client is not None else None
        if host not in ("127.0.0.1", "::1"):
            return JSONResponse(status_code=403, content={"ok": False, "msg": "仅限本机访问"})
        # DNS Rebinding 防护：仅凭 client.host 可被恶意域名绕过（域名解析到 127.0.0.1），
        # 需额外校验 Host 头只允许本机主机名。所有 /api/* 均经此处（含 dsh_approval/dsh_step 双保险）。
        req_host = (request.headers.get("host") or "").strip()
        if req_host:
            if req_host.startswith("["):
                req_host = req_host[1:req_host.find("]")]
            else:
                req_host = req_host.split(":")[0]
            req_host = req_host.lower()
            if req_host not in ("127.0.0.1", "localhost", "::1"):
                return JSONResponse(status_code=403, content={"ok": False, "msg": "非法 Host 头"})
    return await call_next(request)

pipeline: Pipeline | None = None
tts = None
bridge: DSHBridge | None = None
perms: Perms | None = None
authorizations: AuthorizationCenter | None = None
tasks: TaskManager | None = None
auditor = None  # type: ignore[assignment]  # T8 可审计回放订阅方（startup 构建）


@app.on_event("startup")
async def startup() -> None:
    global pipeline, tts, bridge, perms, authorizations, tasks, auditor
    loop = asyncio.get_running_loop()

    llm = build_llm()
    tts = build_tts()
    router = Router()
    auditor = build_auditor()

    # T8 可审计回放：bridge 的 event_sink 事件追加式记录为 run 级 fact plane。
    # 前端上屏 event（work_step/dsh_chunk）保持原样，原始事件另喂 auditor 只读追加。
    def _bridge_sink(kind: str, payload: dict) -> None:
        if kind in ("work_step", "dsh_chunk"):
            emit(kind, **payload)
        auditor.handle_event(kind, payload)

    bridge = build_bridge(event_sink=_bridge_sink)
    perms = Perms()
    authorizations = AuthorizationCenter()
    tasks = TaskManager(bridge)
    pipeline = Pipeline()

    agent = Agent(
        llm,
        tts,
        registry,
        set_state=pipeline.set_state,
        on_done=pipeline.on_agent_done,
    )
    pipeline.attach(agent, tts, router=router, bridge=bridge, perms=perms, tasks=tasks)

    async def notify(text: str) -> None:
        emit("reminder_fired", text=text)
        pipeline.set_state(State.SPEAKING)
        await tts.speak(text)
        pipeline.on_agent_done()

    register_builtin_tools(on_reminder_fire=notify)
    computer.set_confirm_hook(pipeline.request_tool_approval)
    open_app.set_confirm_hook(pipeline.request_tool_approval)
    pipeline.start(loop)
    # M1 记忆定时兜底（daemon 长间隔治理，幂等）
    try:
        from backend.memv1.maintenance import start_sweeper

        start_sweeper()
    except Exception:  # noqa: BLE001
        pass


@app.on_event("shutdown")
async def shutdown() -> None:
    """优雅关停：结束音频线程、取消挂起的审批，并释放 TTS/桥接/任务资源，避免进程残留占用水。"""
    if pipeline is not None:
        pipeline.stop()
    if bridge is not None:
        try:
            bridge.cancel()
        except Exception:
            pass
        try:
            bridge.shutdown()
        except Exception:
            pass
    if tts is not None:
        for release in (getattr(tts, "stop", None), getattr(tts, "close", None)):
            if release is None:
                continue
            try:
                release()
            except Exception:
                pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # 回环校验：与 /api/* 中间件同一安全红线，/ws 也仅接受本机连接
    if ws.client is not None and ws.client.host not in ("127.0.0.1", "::1"):
        await ws.close(code=1008)
        return
    # DNS Rebinding 防护：同 /api 中间件，校验 Host 头只允许本机主机名
    _req_host = (ws.headers.get("host") or "").strip()
    if _req_host:
        if _req_host.startswith("["):
            _req_host = _req_host[1:_req_host.find("]")]
        else:
            _req_host = _req_host.split(":")[0]
        _req_host = _req_host.lower()
        if _req_host not in ("127.0.0.1", "localhost", "::1"):
            await ws.close(code=1008)
            return
    await ws.accept()
    out_q: asyncio.Queue = asyncio.Queue()

    def _push(event: dict) -> None:
        try:
            out_q.put_nowait(event)
        except Exception:
            pass

    unsub = bus.subscribe(_push)

    async def sender() -> None:
        while True:
            event = await out_q.get()
            await ws.send_json(event)

    async def receiver() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type")
            if t == "wake":
                pipeline.wake_manually()
            elif t == "text":
                images = msg.get("images")
                pipeline.submit_text(msg.get("text", ""), images if isinstance(images, list) else None)
            elif t == "router_mode":
                pipeline.set_router_mode(msg.get("mode", "auto"))
            elif t == "approval_answer":
                # 屏幕按钮直接回填语音审批（不进语音识别，规避回声/噪音误判）
                pipeline.answer_approval(str(msg.get("decision", "")))
            elif t == "interrupt":
                pipeline.interrupt()
            elif t == "storage_action":
                # 存储满弹窗：用户选「清理旧记忆」→ 后台容量清理（P0 永不失效）
                if str(msg.get("action", "")) == "clean":
                    try:
                        from backend.memv1.maintenance import clean_now

                        result = clean_now()
                        n = int(result.get("invalidated", 0)) if isinstance(result, dict) else 0
                        emit("storage_cleaned", invalidated=n)
                    except Exception:  # noqa: BLE001
                        pass
            elif t == "ping":
                await ws.send_json({"type": "pong"})

    try:
        await asyncio.gather(sender(), receiver())
    except WebSocketDisconnect:
        pass
    finally:
        unsub()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "state": pipeline.state.value if pipeline else "stopped"}


@app.get("/api/config")
async def get_config() -> dict:
    return {"ok": True, "config": config.get_all()}


@app.post("/api/config")
async def update_config(payload: dict) -> dict:
    updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else payload
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "msg": "无效的配置数据"}
    err = validate_config_updates(updates)
    if err:
        return {"ok": False, "msg": err}
    config.update(updates)
    config.save()
    if pipeline is not None:
        pipeline.reload_soft()
    return {"ok": True, "config": config.get_all()}


@app.get("/api/config/schema")
async def get_config_schema() -> dict:
    """设置字段注册表：前端据此自动渲染（类型/选项/分组/是否需重启）。"""
    from backend.settings_schema import GROUPS, SCHEMA

    return {"ok": True, "groups": GROUPS, "fields": SCHEMA}


@app.get("/api/audio/devices")
async def audio_devices() -> dict:
    """枚举输入/输出音频设备（麦克风、扬声器）。"""
    import sounddevice as sd

    try:
        hostapis = sd.query_hostapis()
        inputs, outputs = [], []
        devs = sd.query_devices()
        default_in, default_out = sd.default.device
        for i, d in enumerate(devs):
            entry = {
                "index": i,
                "name": str(d.get("name", "")),
                "is_default": i == default_in or i == default_out,
            }
            if int(d.get("max_input_channels", 0)) > 0:
                inputs.append(entry)
            if int(d.get("max_output_channels", 0)) > 0:
                outputs.append(entry)
        return {"ok": True, "inputs": inputs, "outputs": outputs, "hostapis": len(hostapis)}
    except Exception as e:  # noqa: BLE001
        print(f"[audio/devices] 枚举失败: {type(e).__name__}: {e}")
        return {"ok": False, "msg": f"枚举音频设备失败：{human_reason(e, default='请检查音频设备连接与驱动后重试')}"}


PREVIEW_TEXT = (
    "2026年8月27日，欢迎来到小二的频道！重庆的重量级嘉宾已到达，"
    "Wi-Fi 信号满格，电量还剩百分之八十六。请问，需要我现在播放音乐吗？"
)
PREVIEW_DIR = ROOT / ".tmp" / "previews"
PREVIEW_RE = re.compile(r"^[0-9a-f]{16}\.(mp3|wav)$")


def _preview_cache_path(fingerprint: str, ext: str) -> Path:
    """缓存文件名：sha1(引擎指纹|文本) 前 16 位 + 音频后缀（文件名不含用户输入，防路径穿越）。"""
    key = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    return PREVIEW_DIR / f"{key}{ext}"


def _prune_previews(keep: int = 200) -> None:
    """缓存超限清理：按修改时间只保留最近 keep 个试听音频，防止无限膨胀。"""
    try:
        files = sorted(PREVIEW_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/tts/preview")
async def tts_preview(payload: dict) -> dict:
    """试听：固定测试句按方案/音色合成一次并落盘缓存，返回音频 URL 由前端 <audio> 播放。

    优先按指定方案（model）构建一次性实例；未指定时回退当前激活引擎。
    同一「引擎指纹 + 文本」只合成一次，之后直接命中本地缓存（没有 Key 也能重听）。
    用完即释放（close/stop），避免云引擎的连接池被试听反复占用。
    """
    voice = payload.get("voice")
    rate = payload.get("rate")
    model = payload.get("model")
    text = str(payload.get("text") or PREVIEW_TEXT).strip()
    engine = None
    try:
        engine = build_preview_tts(
            model=model if isinstance(model, dict) else None,
            voice=str(voice) if voice else None,
            rate=str(rate) if rate else None,
        )
        # 缓存命中先行：合成过的音色重听不建连、不耗 Key（放在 preflight 之前）
        cache_file = _preview_cache_path(f"{engine.cache_fingerprint()}|{text}", engine.audio_ext)
        if cache_file.is_file():
            return {"ok": True, "url": f"/api/tts/preview-audio/{cache_file.name}", "cached": True}
        problem = engine.preflight()
        if problem:
            return {"ok": False, "msg": problem}
        data = await asyncio.wait_for(engine.synthesize(text), timeout=30)
        if not data:
            return {"ok": False, "msg": "试听失败: 引擎未返回音频"}
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
        _prune_previews()
        return {"ok": True, "url": f"/api/tts/preview-audio/{cache_file.name}", "cached": False}
    except NotImplementedError:
        return {"ok": False, "msg": "该引擎暂不支持纯合成试听"}
    except asyncio.TimeoutError:
        engine.stop()  # to_thread 无法取消，靠 stop/close 让合成线程退出
        return {"ok": False, "msg": "试听超时/连接失败：请检查该方案的 API Key 与网络后重试"}
    except Exception as e:  # noqa: BLE001
        print(f"[tts/preview] 合成失败: {type(e).__name__}: {e}")
        return {"ok": False, "msg": f"试听失败：{human_reason(e, default='请检查该方案的 Key、模型名与网络后重试')}"}
    finally:
        if engine is not None:
            release = getattr(engine, "close", None) or getattr(engine, "stop", None)
            if release is not None:
                try:
                    release()
                except Exception:
                    pass


@app.get("/api/tts/preview-audio/{fname}")
async def tts_preview_audio(fname: str):
    """返回试听缓存音频（文件名限定 16 位十六进制 + mp3/wav，防路径穿越）。"""
    if not PREVIEW_RE.match(fname):
        raise HTTPException(status_code=404, detail="not found")
    path = PREVIEW_DIR / fname
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media)


@app.post("/api/provider/test")
async def provider_test(payload: dict) -> dict:
    """连通性测试：按环节（llm/asr/tts）+ 方案字段发最小请求，秒级返回 ok 与人话原因。

    body: {target: "llm"|"asr"|"tts", model: {provider, model, baseUrl, apiKey, ...}}
    返回: {ok, msg, latency_ms} —— msg 永远是人能看懂的提示（Key 无效/超额/超时各有一句话）。
    """
    return await test_provider(payload.get("target"), payload.get("model"))


@app.get("/api/health/probe")
async def health_probe() -> dict:
    """健康状态灯（E4）：逐项探测 ASR / LLM / TTS / agent（DSH）连通状态 + 离线就绪（v3）。

    ASR/LLM/TTS 按当前激活方案复用 E2b 的 test_provider 发最小请求（并行，几秒内齐）；
    agent（DSH）只查本机能否找到 dsh 命令（is_available，不实际拉起，秒回）；
    offline 只读配置判四环节是否全本地（零网络，随灯组一起返回）。
    返回 items: [{key, label, scheme, ok, msg, latency_ms}] —— msg 永远是人话，
    红灯项照着 msg 处理即可（缺 Key/超额/超时各有一句话）。
    """
    from backend.offline import offline_item
    from backend.provider_test import agent_item, probe_component, resolve_active

    async def _one(key: str) -> dict:
        m, scheme = resolve_active(config.get_all(), key)
        return await probe_component(key, m, scheme)

    asr, llm, tts = await asyncio.gather(_one("asr"), _one("llm"), _one("tts"))
    agent_ok = bool(bridge.is_available()) if bridge is not None else False
    return {"ok": True, "items": [asr, llm, tts, agent_item(agent_ok), offline_item(config.get_all())]}


@app.get("/api/memory/list")
async def memory_list() -> dict:
    """长期记忆清单（只读）：跨会话记住的内容，供设置面板查看。"""
    from backend.memory import memory_store

    entries = memory_store.entries()
    return {"ok": True, "count": len(entries), "entries": entries}


@app.post("/api/memory/clear")
async def clear_memory() -> dict:
    """一键清空记忆：当前对话（Agent 历史 + DSH 上下文）+ 跨会话长期记忆。"""
    if pipeline is None:
        return {"ok": False, "msg": "未初始化"}
    pipeline.clear_memory()
    return {"ok": True}


@app.post("/api/memory/delete")
async def delete_memory(payload: dict) -> dict:
    """删除单条长期记忆（按 id，部分选择，非一键全清）。body: {id}。"""
    from backend.memory import memory_store

    entry_id = str(payload.get("id") or "").strip()
    if not entry_id:
        return {"ok": False, "msg": "缺少要删除的记忆 id"}
    if not memory_store.delete(entry_id):
        return {"ok": False, "msg": "未找到该条记忆（可能已被删除）"}
    return {"ok": True, "deleted": entry_id}


@app.post("/api/memory/delete_range")
async def delete_memory_range(payload: dict) -> dict:
    """按时间区间删除长期记忆（快捷项由前端换算成 ts 传入）。body: {start_ts?, end_ts?}。"""
    from backend.memory import memory_store

    start_ts = payload.get("start_ts")
    end_ts = payload.get("end_ts")
    try:
        lo = None if start_ts in (None, "") else float(start_ts)
        hi = None if end_ts in (None, "") else float(end_ts)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "时间戳格式不正确（需为 Unix 秒数字）"}
    if lo is not None and hi is not None and lo > hi:
        return {"ok": False, "msg": "开始时间不能晚于结束时间"}
    removed = memory_store.delete_range(lo, hi)
    return {"ok": True, "removed": removed}


@app.post("/api/audit/clear")
async def clear_audit() -> dict:
    """清空审计 fact plane（run 级事实日志）。"""
    if auditor is None:
        return {"ok": False, "msg": "审计未初始化"}
    runs = auditor.plane.runs()
    auditor.flush()
    auditor.plane.clear()
    return {"ok": True, "cleared_runs": len(runs)}


@app.post("/api/persona/clear")
async def clear_persona() -> dict:
    """清空用户画像：人设卡/亲友卡/哀伤标签/惯例画像（世界观 lorebook 保留）。"""
    from backend.memv1.persona import clear_persona as persona_clear

    try:
        persona_clear()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"清空画像失败：{exc}"}
    return {"ok": True}


@app.post("/api/memv4/clear")
async def clear_memv4() -> dict:
    """清空会话数据轨（session_logs / raw_frames_meta / context_snapshots）。

    走 memv4 模块单例，清空后 agent 注入的会话原文 provider 立即同步（无需重启）。
    """
    from backend.memv4 import get_datatrack

    removed = get_datatrack().clear()
    return {"ok": True, "removed": removed}


@app.post("/api/memv1/profile/clear")
async def clear_profile() -> dict:
    """清空向量画像真源（ProfileStore）+ 同步清空其派生向量索引（kind=episodic）。

    返回 {ok, cleared}；向量索引清理失败不阻断（真源已清，索引可自动重建）。
    """
    from backend.memv1.consolidate import clear_profile as do_clear_profile

    try:
        cleared = do_clear_profile()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"清空画像失败：{exc}"}
    return {"ok": True, "cleared": cleared}


@app.post("/api/mic/echo")
async def mic_echo(payload: dict) -> dict:
    """回声测试：录一段麦克风音频，立即经扬声器回放，验证音频通路。

    body: {duration: 秒（默认 3，上限 10）}
    返回: {ok, duration, peak} —— peak 为录制峰值，过小说明麦克风没录到声。
    """
    import numpy as np
    import sounddevice as sd

    try:
        dur = float(payload.get("duration", 3))
    except (TypeError, ValueError):
        dur = 3.0
    dur = max(1.0, min(dur, 10.0))
    sr = int(config.get("audio.sample_rate", 16000))
    device = config.get("audio.input_device", None)

    def _record_and_play():
        rec = sd.rec(int(dur * sr), samplerate=sr, channels=1, dtype="int16", device=device)
        sd.wait()
        sd.play(rec, samplerate=sr)
        sd.wait()
        peak = float(np.max(np.abs(rec.astype(np.float32)))) if rec.size else 0.0
        return peak

    try:
        peak = await asyncio.to_thread(_record_and_play)
    except Exception as e:  # noqa: BLE001
        print(f"[audio/echo] 测试失败: {type(e).__name__}: {e}")
        return {"ok": False, "msg": f"回声测试失败：{human_reason(e, default='请检查麦克风是否被其他程序占用后重试')}"}
    return {"ok": True, "duration": dur, "peak": round(peak, 1)}


@app.get("/api/status")
async def get_status() -> dict:
    return {
        "ok": True,
        "state": pipeline.state.value if pipeline else "stopped",
        "router_mode": pipeline.router_mode if pipeline else "auto",
        "dsh_available": bridge.is_available() if bridge else False,
    }


@app.get("/api/recall")
async def get_recall() -> dict:
    """成长双轨 + 共同记忆的三栏回顾快照（只读，供前端翻看）。"""
    from backend.m6.growth import GrowthStore
    from backend.m6.recall import RecallComposer

    try:
        return {"ok": True, "data": RecallComposer(GrowthStore()).compose()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}


@app.post("/api/dsh/approval")
async def dsh_approval(payload: dict, request: Request) -> dict:
    """DSH 审批注入端点（🔒 安全红线：只接受本机回环来源，永不外放）。

    DSH 薄插件在 approval/request 瀑布里作为 answerer 调此端点：
    传入 {action} → 小二语音询问「是否允许」→ 阻塞等语音决定 → 返回
    {decision: allowed-once | rejected | unavailable}。
    """
    if request.client is not None and request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="forbidden")
    if pipeline is None:
        return {"ok": False, "decision": "unavailable"}
    action = str(payload.get("action", "")).strip() or "该操作"
    decision = await pipeline.request_approval(action)
    return {"ok": True, "decision": decision}


@app.get("/api/tasks")
async def get_tasks() -> dict:
    """任务面板：后台任务列表（最近 30 条）。"""
    return {"ok": True, "tasks": tasks.list() if tasks else []}


@app.post("/api/dsh/step")
async def dsh_step(payload: dict, request: Request) -> dict:
    """DSH 插件实时上报工具步骤（🔒 安全红线：只接受本机回环来源，永不外放）。

    DSH 薄插件在 tools/result 事件里 POST 此端点，转发为前端可见的 work_step 事件：
    传 {name: 工具名, status: start|done|error, summary: 摘要}。
    """
    if request.client is not None and request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="forbidden")
    name = str(payload.get("name") or "tool")
    status = str(payload.get("status") or "run")
    summary = str(payload.get("summary") or "").strip()
    emit("work_step", name=name, status=status, summary=summary)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    ok = tasks.cancel(task_id) if tasks else False
    return {"ok": ok}


@app.get("/api/perms")
async def get_perms() -> dict:
    """权限面板：分类清单 + 常驻授权 + 待授权任务。"""
    return {
        "ok": True,
        "categories": [{"id": c, "label": l, "desc": d} for c, l, d in CATEGORIES],
        "standing": perms.standing() if perms else [],
        "deferred": perms.list_deferred() if perms else [],
    }


@app.post("/api/perms/standing")
async def set_standing(payload: dict) -> dict:
    category = str(payload.get("category", ""))
    granted = bool(payload.get("granted", False))
    if perms is None:
        return {"ok": False, "msg": "未初始化"}
    try:
        perms.set_granted(category, granted)
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "standing": perms.standing()}


@app.post("/api/perms/deferred/{item_id}")
async def decide_deferred(item_id: str, payload: dict) -> dict:
    approved = bool(payload.get("approved", False))
    if perms is None:
        return {"ok": False, "msg": "未初始化"}
    item = perms.get_deferred(item_id)
    if item is None:
        return {"ok": False, "msg": "任务不存在"}
    # 先原子消费决定（仅 pending 态返回 True），防止重复点击重放授权与重执行
    if not perms.decide_deferred(item_id, approved):
        return {"ok": False, "msg": "任务已处理过"}
    if approved:
        # 允许 = 把这些分类纳入常驻授权（授权有记忆）+ 重新提交执行
        for c in item.get("needed", []):
            try:
                perms.set_granted(c, True)
            except ValueError:
                pass
        if pipeline is not None and item.get("text"):
            pipeline.submit_text(str(item["text"]))
    return {"ok": True, "deferred": perms.list_deferred()}


@app.get("/api/authorizations")
async def get_authorizations() -> dict:
    """授权中心：授权项视图（统一收口，可查看）。

    与 /api/perms 同级保护：仅本机可访问；授权项**不能**经 /api/config 改写
    （config_guard 因未在 settings_schema 登记而拒绝），写入走 /api/authorizations/set。
    """
    return {
        "ok": True,
        "authorizations": authorizations.get() if authorizations else {},
        "items": [
            {"key": i["key"], "type": i["type"], "label": i["label"],
             "default": i["default"], "desc": i.get("desc", "")}
            for i in AUTHORIZATION_ITEMS
        ],
    }


@app.post("/api/authorizations/set")
async def set_authorization(payload: dict) -> dict:
    """授权项设值（提权操作，走专用端点；非法取值拒绝）。

    body: {key, value}。per_feature 亦可传完整 {key -> bool} 映射；
    单项授权用 /api/authorizations/set_feature。
    """
    if authorizations is None:
        return {"ok": False, "msg": "未初始化"}
    key = str(payload.get("key", ""))
    value = payload.get("value")
    try:
        state = authorizations.set(key, value)
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "authorizations": state}


@app.post("/api/authorizations/set_feature")
async def set_feature(payload: dict) -> dict:
    """细项授权单项开/关（写入 per_feature）。body: {feature, granted}。"""
    if authorizations is None:
        return {"ok": False, "msg": "未初始化"}
    feature = str(payload.get("feature", ""))
    if not feature:
        return {"ok": False, "msg": "缺少 feature"}
    state = authorizations.set_feature(feature, bool(payload.get("granted", False)))
    return {"ok": True, "authorizations": state}


@app.post("/api/authorizations/revoke")
async def revoke_authorization(payload: dict) -> dict:
    """授权项撤回（恢复出厂默认全关）。body: {key}。"""
    if authorizations is None:
        return {"ok": False, "msg": "未初始化"}
    key = str(payload.get("key", ""))
    try:
        state = authorizations.revoke(key)
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "authorizations": state}


# 生产模式：托管前端构建产物（如已构建 frontend/dist）
dist = Path(ROOT) / "frontend" / "dist"
if dist.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
