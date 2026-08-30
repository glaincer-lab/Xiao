"""授权中心（M0 · 结构规划 S5 / M0-core §3）：统一收口敏感能力授权项。

授权项（camera_enabled / screen_awareness / proactivity_level /
emergency_passthrough / per_feature）在此集中登记（AUTHORIZATION_ITEMS），默认全关
（传感默认关，见 PRODUCT 红线）。

设计定位（安全边界）：
    - 授权项**不在** backend.settings_schema.SCHEMA 登记 —— 因此 config_guard 的
      allowed_config_paths()（只来自 SCHEMA）会把 `authorizations.*` 判为「未知路径」，
      天然**拒绝**经 /api/config 改写（提权段保护，与 T2 的 perms 同源策略）；
      写入只能走专用 /api/authorizations/* 端点。
    - 可查看（get()）、可设值（set()）、可撤回（revoke()）统一在此收口。
    - 本模块是 config.yaml 中 `authorizations` 段的唯一写者，采用「整段替换」写回，
      避免 Config.update 深合并对空 dict（per_feature 撤回）无法清空的语义问题。
    - 本模块只依赖 backend.config（基础设施），跨模块通信一律走 event_bus，
      不直调其它模块核心函数（模块边界纪律 T0/S6）。

消费方（待 M3 主动引擎 / M4 视觉接入后读取）：
    - camera_enabled / screen_awareness：M4 视觉能力闸门（默认关）。
    - proactivity_level：M3 主动行为总闸门（默认 0 = 全关）。
    - emergency_passthrough：紧急场景穿透审批清单（默认空 = 不穿透）。
    - per_feature：各功能细项授权注册表（默认空 = 全部未授权）。
"""
from __future__ import annotations

from typing import Any

from backend.config import config as default_config


def _copy(value: Any) -> Any:
    """深拷贝可变值（list/dict），避免 get() 对外暴露 config 内部引用而误改。"""
    import copy

    return copy.deepcopy(value)


# 授权项集中登记（单一事实来源）：默认全关。
# type: bool | int | list | dict —— 按 type 校验；default 为「出厂/撤回」值。
AUTHORIZATION_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "key": "camera_enabled",
        "type": "bool",
        "label": "摄像头访问",
        "default": False,
        "desc": "是否允许读取摄像头画面（默认关闭；开启需用户明确同意）",
    },
    {
        "key": "screen_awareness",
        "type": "bool",
        "label": "屏幕感知",
        "default": False,
        "desc": "是否允许截屏/读屏感知（默认关闭）",
    },
    {
        "key": "proactivity_level",
        "type": "int",
        "label": "主动程度",
        "default": 0,
        "min": 0,
        "max": 100,
        "desc": "主动行为总闸门滑块：0 为全关，100 为最大主动（默认 0）",
    },
    {
        "key": "emergency_passthrough",
        "type": "list",
        "label": "紧急穿透清单",
        "default": [],
        "desc": "用户可配置的紧急场景清单（命中时允许穿透审批；默认空 = 不穿透）",
    },
    {
        "key": "per_feature",
        "type": "dict",
        "label": "细项授权",
        "default": {},
        "desc": "各功能细项授权注册表（默认空 = 全部未授权；值为功能 key -> bool）",
    },
)

# 出厂默认（撤回即回到此值）
DEFAULT_AUTHORIZATIONS: dict[str, Any] = {item["key"]: item["default"] for item in AUTHORIZATION_ITEMS}

ITEMS_BY_KEY: dict[str, dict[str, Any]] = {item["key"]: item for item in AUTHORIZATION_ITEMS}


class AuthorizationCenter:
    """授权中心：视图 / 设值 / 撤回 + 安全校验。

    cfg 可注入（测试用内存 fake），默认使用 backend.config.config。
    """

    SECTION = "authorizations"

    def __init__(self, cfg: Any | None = None) -> None:
        if cfg is None:
            cfg = default_config
        self._cfg = cfg

    # ---------- 视图 ----------
    def get(self) -> dict[str, Any]:
        """返回当前授权状态（默认全关合并实际配置；缺项补默认，均为深拷贝安全值）。"""
        current = self._cfg.get(self.SECTION) or {}
        if not isinstance(current, dict):
            current = {}
        out: dict[str, Any] = {}
        for key, item in ITEMS_BY_KEY.items():
            if key in current:
                out[key] = _copy(current[key])
            else:
                out[key] = item["default"]
        return out

    def get_item(self, key: str) -> Any:
        return self.get().get(key, self._item(key)["default"])

    def is_granted(self, key: str) -> bool:
        """布尔授权项（camera/screen）是否已开启。"""
        item = self._item(key)
        if item["type"] != "bool":
            raise ValueError(f"{key} 不是布尔授权项，无法用 is_granted 判定")
        return bool(self.get_item(key))

    def is_feature_granted(self, feature: str) -> bool:
        """细项授权（per_feature）里某功能是否已开启。"""
        pf = self.get_item("per_feature")
        if not isinstance(pf, dict):
            return False
        return bool(pf.get(str(feature), False))

    # ---------- 校验 ----------
    def validate(self, key: str, value: Any) -> None:
        """校验授权项取值；非法则抛 ValueError（与 Perms.set_granted 一致）。"""
        item = self._item(key)
        typ = item["type"]
        if typ == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"授权项 {key} 需为布尔值（True/False）")
        elif typ == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"授权项 {key} 需为整数")
            lo, hi = item.get("min", float("-inf")), item.get("max", float("inf"))
            if not (lo <= value <= hi):
                raise ValueError(f"授权项 {key} 需在 {lo}~{hi} 之间")
        elif typ == "list":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"授权项 {key} 需为字符串列表")
        elif typ == "dict":
            if not isinstance(value, dict):
                raise ValueError(f"授权项 {key} 需为字典（功能 key -> bool）")
            if not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
                raise ValueError(f"授权项 {key} 各细项需为 key(str) -> value(bool) 映射")
        else:  # pragma: no cover - 登记项类型受控
            raise ValueError(f"未知授权项类型: {typ}")

    # ---------- 写 ----------
    def set(self, key: str, value: Any) -> dict[str, Any]:
        """设值并持久化（合法后整段替换写回），返回新状态。"""
        self.validate(key, value)
        section = self.get()
        section[key] = _copy(value)
        self._commit(section)
        return section

    def revoke(self, key: str) -> dict[str, Any]:
        """撤回：恢复出厂默认（全关），返回新状态。"""
        item = self._item(key)
        return self.set(key, item["default"])

    def set_feature(self, feature: str, granted: bool) -> dict[str, Any]:
        """细项授权：单项开/关（写入 per_feature），返回新状态。"""
        section = self.get()
        pf = section.get("per_feature") or {}
        if not isinstance(pf, dict):
            pf = {}
        pf = dict(pf)
        pf[str(feature)] = bool(granted)
        section["per_feature"] = pf
        self._commit(section)
        return section

    def revoke_feature(self, feature: str) -> dict[str, Any]:
        """细项授权撤回（单项关闭）。"""
        return self.set_feature(feature, False)

    # ---------- 内部 ----------
    def _item(self, key: str) -> dict[str, Any]:
        if key not in ITEMS_BY_KEY:
            raise ValueError(f"未知授权项: {key}")
        return ITEMS_BY_KEY[key]

    def _commit(self, section: dict[str, Any]) -> None:
        """整段替换写入 authorizations 段（本模块是唯一写者）。

        Config.update 采用深合并，对 dict 空值无法清空既有内容（如 per_feature 撤回 {}）；
        这里直接替换整段，保证「撤回=出厂默认」语义可靠。
        """
        sec = dict(section)
        if hasattr(self._cfg, "_data"):
            self._cfg._data[self.SECTION] = sec
        else:  # 退化：无 _data 的可注入 fake
            self._cfg.update({self.SECTION: sec})
        self._cfg.save()


__all__ = ["AUTHORIZATION_ITEMS", "DEFAULT_AUTHORIZATIONS", "AuthorizationCenter"]
