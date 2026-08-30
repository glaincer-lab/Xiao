"""出网安全网关 · 语义消歧（可选增强，M0）。

对「已命中黑词」的文本做语境判断：是「技术/安全语境」还是「真实敏感数据/自伤」，
从而避免「密码学 / 密钥管理」这类正常词被纯正则误拦。

角色（不替代黑词表硬闸）：
- 黑词表命中 = 确定性拦截（fail-closed，见 A2 blocklist）；
- 本模块只做「放行器」：命中黑词后用本地小模型判断语境，明确技术语境才放行；
- 模型缺失 / 推理失败 / 不确定 -> 返回 "unknown"，由上层（A5 编排）回退到规则
  （allow_words）判断，绝不裸奔出网。因此**模型没装也不影响开箱即用**。

实现：onnxruntime（已在 requirements）+ 两种 tokenizer 路径：
  1) 优先用 tokenizers 库 + tokenizer.json（更准）；
  2) 库缺失时回退到内置标准库 BERT WordPiece tokenizer（读 tokenizer.json，够用）。
模型目录默认 models/gateway-semantic/<model_name>/（model.onnx + tokenizer.json + config.json）。
遵循 AGENTS.md：路径相对、报错人话、缺失自动降级、MIT 自写代码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from backend.gateway.constants import SELF_HARM_KEYWORDS

try:
    import onnxruntime as ort
except Exception:  # pragma: no cover - 环境缺依赖时的兜底
    ort = None

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = _PROJECT_ROOT / "models" / "gateway-semantic"
DEFAULT_THRESHOLD = 0.72

# 内置「安全技术语境」锚点（命中黑词后与之比较相似度；可经 cfg['anchors'] 增补）。
DEFAULT_ANCHORS = [
    "密码学", "密钥管理", "重置密码", "加密算法", "数字证书", "身份认证",
    "信息安全", "信息安全管理", "API密钥", "密钥对", "加解密", "SSL证书",
    "HTTPS证书", "TLS协议", "数据库密码", "安全工程师",
]

_REDLINE = SELF_HARM_KEYWORDS


class SemanticUnavailable(RuntimeError):
    """语义模型不可用（缺依赖/缺模型/推理失败），调用方应回退规则。"""


# ---------------------------------------------------------------------------
# 内置标准库 BERT WordPiece tokenizer（tokenizers 库缺失时的回退）
# ---------------------------------------------------------------------------
_CJK = re.compile(r"[一-鿿㐀-䶿]")
_PRETOKEN = re.compile(r"[A-Za-z0-9]+|[一-鿿㐀-䶿]|[^sA-Za-z0-9一-鿿㐀-䶿]")


class BuiltinBertTokenizer:
    """基于 tokenizer.json 的标准库 WordPiece 编码（简化版，无第三方依赖）。"""

    def __init__(self, tokenizer_json) -> None:
        path = Path(tokenizer_json)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SemanticUnavailable(f"无法解析 tokenizer.json：{exc}") from exc
        vocab = data.get("model", {}).get("vocab", {})
        self.vocab: dict[str, int] = vocab
        self.unk_id = vocab.get("[UNK]", 100)
        self.cls_id = vocab.get("[CLS]", 101)
        self.sep_id = vocab.get("[SEP]", 102)
        self.pad_id = vocab.get("[PAD]", 0)
        self.max_len = 512

    def _pieces(self, token: str) -> list[str]:
        if token in self.vocab:
            return [token]
        # 标准 WordPiece：从左到右最长子词（续接用 ## 前缀）
        out: list[str] = []
        start = 0
        while start < len(token):
            end = len(token)
            cur = None
            while end > start:
                sub = token[start:end]
                piece = sub if start == 0 else "##" + sub
                if piece in self.vocab:
                    cur = piece
                    break
                end -= 1
            if cur is None:
                return [self.vocab and "[UNK]"] and ["[UNK]"]
            out.append(cur)
            start = end
        return out

    def encode(self, text: str):
        """返回带 .ids/.attention_mask/.token_type_ids 的对象。"""
        tokens = _PRETOKEN.findall(text or "")
        tok_ids = [self.cls_id]
        for tok in tokens:
            for p in self._pieces(tok):
                tok_ids.append(self.vocab.get(p, self.unk_id))
        tok_ids.append(self.sep_id)
        if len(tok_ids) > self.max_len:
            tok_ids = tok_ids[: self.max_len - 1] + [self.sep_id]
        n = len(tok_ids)
        return _Enc(tok_ids, [1] * n, [0] * n)


class _Enc:
    def __init__(self, ids, attention_mask, token_type_ids):
        self.ids = ids
        self.attention_mask = attention_mask
        self.token_type_ids = token_type_ids


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
def _context(cfg: dict | None) -> dict:
    if cfg and isinstance(cfg.get("semantic"), dict):
        return cfg["semantic"]
    return {}


def _model_dir(cfg: dict | None) -> Path:
    rel = _context(cfg).get("model_dir", "models/gateway-semantic")
    p = Path(rel)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _model_name(cfg: dict | None) -> str:
    return _context(cfg).get("model_name", "bge-small-zh-v1.5")


def _threshold(cfg: dict | None) -> float:
    try:
        return float(_context(cfg).get("threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def _anchors(cfg: dict | None) -> list[str]:
    extra = _context(cfg).get("anchors", [])
    return list(DEFAULT_ANCHORS) + list(extra if isinstance(extra, list) else [])


# ---------------------------------------------------------------------------
# ONNX 推理引擎
# ---------------------------------------------------------------------------
class _Engine:
    def __init__(self, cfg: dict | None) -> None:
        self._cfg = cfg
        self._session = None
        self._tokenizer = None
        self._error: str | None = None
        self._anchor_vec: dict[str, np.ndarray] = {}

    def _find_model(self, base: Path) -> Path | None:
        for name in ("model.onnx", "model_quantized.onnx"):
            p = base / name
            if p.is_file():
                return p
        cands = sorted(base.glob("*.onnx"))
        return cands[0] if cands else None

    def _load(self) -> bool:
        if self._session is not None and self._tokenizer is not None:
            return True
        if ort is None:
            self._error = "缺少 onnxruntime，请先 pip install onnxruntime"
            return False
        base = _model_dir(self._cfg) / _model_name(self._cfg)
        model_path = self._find_model(base)
        if model_path is None:
            self._error = f"未找到语义模型（{base} 下没有 .onnx）。请把下载的 model.onnx / model_quantized.onnx 放入该目录。"
            return False
        try:
            self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        except Exception as exc:
            self._error = f"语义模型加载失败：{exc}"
            return False
        self._tokenizer = self._build_tokenizer(base)
        if self._tokenizer is None:
            self._error = f"缺少可用的 tokenizer（{base}/tokenizer.json）"
            return False
        return True

    def _build_tokenizer(self, base: Path):
        tok_json = base / "tokenizer.json"
        if tok_json.is_file():
            try:
                from tokenizers import Tokenizer
                return Tokenizer.from_file(str(tok_json))
            except Exception:
                pass
            try:
                return BuiltinBertTokenizer(tok_json)
            except Exception:
                return None
        return None

    def _tokenize(self, text: str):
        enc = self._tokenizer.encode(text)
        ids = getattr(enc, "ids", None)
        mask = getattr(enc, "attention_mask", None)
        types = getattr(enc, "token_type_ids", None)
        if ids is None:
            raise SemanticUnavailable("tokenizer 无法编码")
        out_ids = np.array([ids], dtype=np.int64)
        out_mask = np.array([mask], dtype=np.int64) if mask is not None else np.ones_like(out_ids)
        out_types = np.zeros_like(out_ids) if types is None else np.array([types], dtype=np.int64)
        name_by_role = {}
        for n in self._session.get_inputs():
            name_by_role[n.name.lower()] = n.name
        feeds: dict[str, np.ndarray] = {}
        for key, arr in (("input_ids", out_ids), ("attention_mask", out_mask), ("token_type_ids", out_types)):
            name = name_by_role.get(key)
            if name:
                feeds[name] = arr
        return out_mask, feeds

    def _embed(self, text: str) -> np.ndarray:
        out_mask, feeds = self._tokenize(text)
        outs = self._session.run(None, feeds)
        v = np.asarray(outs[0], dtype=np.float32)
        if v.ndim == 3:
            mask_exp = np.expand_dims(out_mask, -1).astype(np.float32)
            v = (v * mask_exp).sum(axis=1) / np.maximum(out_mask.sum(axis=1, keepdims=True), 1)
        v = v[0]
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        return v

    def is_available(self) -> bool:
        return self._load()

    def error_reason(self) -> str | None:
        self._load()
        return self._error

    def _window(self, text: str, hit_word: str, radius: int = 10) -> str:
        """取命中词局部窗口（±radius 字符），避免整句被无关技术锚点抬高相似度。"""
        if not hit_word:
            return text
        idx = text.find(hit_word)
        if idx < 0:
            return text
        start = max(0, idx - radius)
        end = min(len(text), idx + len(hit_word) + radius)
        return text[start:end]

    def judge(self, text: str, hit_word: str) -> str:
        # 自伤红线：无条件扫全文，任何语境不放行（不依赖 hit_word 是否为红线词）。
        if any(rw in text for rw in _REDLINE):
            return "block"
        if not self._load():
            return "unknown"
        try:
            v = self._embed(self._window(text, hit_word))
            best = 0.0
            for anchor in _anchors(self._cfg):
                a = self._anchor_vec.get(anchor)
                if a is None:
                    a = self._embed(anchor)
                    self._anchor_vec[anchor] = a
                sim = float(np.dot(v, a))
                if sim > best:
                    best = sim
            if best >= _threshold(self._cfg):
                return "safe"
            return "block"
        except Exception:
            return "unknown"


_ENGINE: dict[tuple, _Engine] = {}


def _engine_key(cfg: dict | None) -> tuple:
    """用稳定身份做引擎缓存键（模型目录/名称/阈值），避免 id(cfg) 随配置对象变化失真。"""
    c = _context(cfg) if cfg else {}
    return (
        c.get("model_dir", "models/gateway-semantic"),
        c.get("model_name", "bge-small-zh-v1.5"),
        float(c.get("threshold", DEFAULT_THRESHOLD)),
    )


def _get_engine(cfg: dict | None) -> _Engine:
    key = _engine_key(cfg)
    eng = _ENGINE.get(key)
    if eng is None:
        eng = _Engine(cfg)
        _ENGINE.clear()  # 单模型场景：只保留当前一套，避免按配置对象无限堆积模型会话
        _ENGINE[key] = eng
    return eng


def is_semantic_available(cfg: dict | None = None) -> bool:
    return _get_engine(cfg).is_available()


def semantic_error(cfg: dict | None = None) -> str | None:
    return _get_engine(cfg).error_reason()


def judge_context(text: str, hit_word: str, cfg: dict | None = None) -> str:
    return _get_engine(cfg).judge(text, hit_word)


__all__ = ["judge_context", "is_semantic_available", "semantic_error"]
