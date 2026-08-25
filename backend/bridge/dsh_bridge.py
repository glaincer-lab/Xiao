"""DSH 桥接：通过 `dsh --profile headless` 调用 DeepSeek Harness 干活。

这是整个语音系统里唯一知道 DSH 存在的模块；其它模块（audio/asr/llm/tts/tools/core）
完全不感知 DSH。未来 DSH 升级、替换，甚至从 headless 切到 API，都只改这里。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

from backend.config import config


class DSHCancelled(Exception):
    """任务被用户主动取消。"""


class DSHBridge:
    def __init__(self) -> None:
        self._workspace = str(config.get("agent.workspace", ".") or ".")
        self._timeout = float(config.get("bridge.timeout_sec", 600))
        self._cmd = self._resolve_command(config.get("bridge.dsh_command", "dsh"))
        self._proc: asyncio.subprocess.Process | None = None
        self._cancelled = False
        # 多轮上下文：记录最近几轮 DSH 任务与结果摘要，每轮打包发给 DSH（headless 无状态，靠这里补记忆）
        self._context: list[dict] = []
        self._context_max = 6

    def reset_context(self) -> None:
        """清空 DSH 多轮上下文（清空历史/退下时调用）。"""
        self._context.clear()

    def _summarize(self, out: str) -> str:
        """压缩 DSH 结果摘要，避免上下文 token 爆炸。"""
        s = " ".join((out or "").split())
        return s[:500] + ("…" if len(s) > 500 else "")

    def _build_prompt(self, task: str) -> str:
        """把历史上下文 + 当前任务拼成一条 prompt 发给 DSH。"""
        if not self._context:
            return task
        parts = ["以下是之前几轮任务的简要记录，请结合上下文理解当前任务："]
        for c in self._context:
            parts.append(f"- 任务：{c['task']}")
            parts.append(f"  结果：{c['result']}")
        parts.append(f"现在的新任务：{task}")
        return "\n".join(parts)

    def is_available(self) -> bool:
        """DSH 命令是否能在本机找到（粗略探测，供前端状态灯显示）。"""
        cmd = config.get("bridge.dsh_command", "dsh")
        if isinstance(cmd, list):
            return bool(cmd)
        s = str(cmd)
        if shutil.which(s):
            return True
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.isfile(os.path.join(d, s + ".ps1")):
                return True
        return False

    def _resolve_command(self, cmd) -> list[str]:
        if isinstance(cmd, list):
            return [str(c) for c in cmd]
        s = str(cmd)
        # Windows 上 dsh 的入口是 .ps1 脚本；.ps1 不在 PATHEXT 里，shutil.which 找不到，
        # 需手动在 PATH 里找 .ps1，找到就用 PowerShell 拉起（dsh.ps1 内部用 @args 透传参数，能正确处理空格/中文）。
        for d in os.environ.get("PATH", "").split(os.pathsep):
            cand = os.path.join(d, s + ".ps1")
            if os.path.isfile(cand):
                ps = shutil.which("pwsh") or "powershell"
                return [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cand]
        # 回退：标准可执行文件（.exe/.cmd/.bat）
        path = shutil.which(s)
        if path:
            if path.lower().endswith((".cmd", ".bat")):
                return ["cmd", "/c", path]
            return [path]
        return [s]

    async def run(self, task: str, *, grant: set[str] | None = None) -> str:
        os.makedirs(self._workspace, exist_ok=True)
        self._cancelled = False
        env = os.environ.copy()
        if grant is not None:
            # 预授权清单传给 DSH 薄插件（xiao-approval-bridge 读 XIAO_GRANT 自动放行匹配项）
            env["XIAO_GRANT"] = json.dumps(sorted(grant))
        prompt = self._build_prompt(task)
        proc = await asyncio.create_subprocess_exec(
            *self._cmd, "--profile", "headless", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workspace,
            env=env,
        )
        self._proc = proc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"DSH 任务超时（超过 {int(self._timeout)} 秒）")
        finally:
            if self._proc is proc:
                self._proc = None

        if self._cancelled:
            raise DSHCancelled()

        out = (stdout or b"").decode("utf-8", "replace").strip()
        err = (stderr or b"").decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(err or f"DSH 退出码 {proc.returncode}")
        if not out:
            raise RuntimeError("DSH 未返回内容" + (f"：{err}" if err else ""))
        # 成功则记录本轮（任务 + 结果摘要）供下一轮做上下文
        self._context.append({"task": task, "result": self._summarize(out)})
        if len(self._context) > self._context_max:
            self._context = self._context[-self._context_max:]
        return out

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
