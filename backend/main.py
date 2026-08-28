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
from backend.bridge.dsh_bridge import DSHBridge
from backend.config import ROOT, config
from backend.core import Pipeline
from backend.llm.factory import build_llm
from backend.perms import CATEGORIES, Perms
from backend.router import Router
from backend.session.state import State, bus, emit
from backend.tasks import TaskManager
from backend.tools import register_builtin_tools
from backend.tools.base import registry
from backend.tts.factory import build_preview_tts, build_tts

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
    return await call_next(request)

pipeline: Pipeline | None = None
tts = None
bridge: DSHBridge | None = None
perms: Perms | None = None
tasks: TaskManager | None = None


@app.on_event("startup")
async def startup() -> None:
    global pipeline, tts, bridge, perms, tasks
    loop = asyncio.get_running_loop()

    llm = build_llm()
    tts = build_tts()
    router = Router()
    bridge = DSHBridge()
    perms = Perms()
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
    pipeline.start(loop)


@app.on_event("shutdown")
async def shutdown() -> None:
    """优雅关停：结束音频线程、取消挂起的审批，避免进程残留音频设备占用。"""
    if pipeline is not None:
        pipeline.stop()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # 回环校验：与 /api/* 中间件同一安全红线，/ws 也仅接受本机连接
    if ws.client is not None and ws.client.host not in ("127.0.0.1", "::1"):
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
                pipeline.submit_text(msg.get("text", ""))
            elif t == "router_mode":
                pipeline.set_router_mode(msg.get("mode", "auto"))
            elif t == "approval_answer":
                # 屏幕按钮直接回填语音审批（不进语音识别，规避回声/噪音误判）
                pipeline.answer_approval(str(msg.get("decision", "")))
            elif t == "interrupt":
                pipeline.interrupt()
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
        return {"ok": False, "msg": f"枚举音频设备失败: {e}"}


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
        return {"ok": False, "msg": f"试听失败: {e}"}
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


@app.post("/api/memory/clear")
async def clear_memory() -> dict:
    """一键清空当前对话记忆（Agent 历史）。"""
    if pipeline is None:
        return {"ok": False, "msg": "未初始化"}
    pipeline.clear_memory()
    return {"ok": True}


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
        return {"ok": False, "msg": f"回声测试失败: {e}"}
    return {"ok": True, "duration": dur, "peak": round(peak, 1)}


@app.get("/api/status")
async def get_status() -> dict:
    return {
        "ok": True,
        "state": pipeline.state.value if pipeline else "stopped",
        "router_mode": pipeline.router_mode if pipeline else "auto",
        "dsh_available": bridge.is_available() if bridge else False,
    }


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


# 生产模式：托管前端构建产物（如已构建 frontend/dist）
dist = Path(ROOT) / "frontend" / "dist"
if dist.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
