"""wall_rules.py —— 墙体【连接规则】的声明式总表（目标2 第三块）。

## 原来散在哪

连接规则本来由三个模块各自用命令式函数一条条注册：

    verticalwall.register_wall_family_corner_rules()   6 条（墙族四角互通）
    floor.register_floor_corner_rules()               8 条（地板角 × 墙族四角）
    connect_demo.register_wall_corner_rules()          4 条（仅 demo 用，与上面重复）

本模块把"墙族用到的连接词汇"收成**一份表**，并提供幂等的 :func:`install`。
原来那两个 register_* 函数改为委托到这里 —— 规则来源从此只有一处，
"到底谁能连谁"看一眼 ALL_RULES 就有答案。

## 与 wall_spec 的分工

    wall_spec.py    行 → 段语义      （生成规则）
    wall_rules.py   谁能连谁          （连接规则）  ← 本模块
    vertical_texture.py  段+连接态 → 贴图（绘制规则）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

__all__ = [
    "ConnectRuleSpec", "WALL_FAMILY_RULES", "FLOOR_CORNER_TYPES",
    "WALL_CORNER_TYPES", "FLOOR_RULES", "ALL_RULES",
    "install", "rule_keys", "describe",
]


@dataclass(frozen=True)
class ConnectRuleSpec:
    """一条可连接的 Port 类型对（外加随 Command 发给两端的动作标签）。

    与 port.ConnectRule 的区别：这个只是**声明**（纯数据，不持有回调）；
    回调在 install 时才绑定，且按"已存在就复用"的既有语义处理。
    """

    type_a: str
    type_b: str
    action: str = "docking"

    @property
    def key(self) -> Tuple[str, str]:
        return tuple(sorted((self.type_a, self.type_b)))   # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"Rule({self.type_a} <-> {self.type_b})"


# =====================================================================
# 墙族四角：横墙四角两两可配（含对角），竖墙复用同一族词汇（见 VerticalWallPort）
# =====================================================================
WALL_FAMILY_RULES: Tuple[ConnectRuleSpec, ...] = (
    ConnectRuleSpec("HorizontalWallTopLeft",     "HorizontalWallTopRight"),
    ConnectRuleSpec("HorizontalWallBottomLeft",  "HorizontalWallBottomRight"),
    ConnectRuleSpec("HorizontalWallBottomLeft",  "HorizontalWallTopLeft"),
    ConnectRuleSpec("HorizontalWallBottomRight", "HorizontalWallTopRight"),
    # 对角：让竖墙既能头连横墙顶角、也能尾连横墙底角，形成包边/转角
    ConnectRuleSpec("HorizontalWallTopLeft",     "HorizontalWallBottomRight"),
    ConnectRuleSpec("HorizontalWallTopRight",    "HorizontalWallBottomLeft"),
)

# =====================================================================
# 地板角：两种角口 × 墙族四角
#   地板角口 max_connections=2（可与两个对端相连，见 floor.FloorCornerPort）
# =====================================================================
FLOOR_CORNER_TYPES: Tuple[str, ...] = ("FloorCorner", "FloorCornerBottom")

WALL_CORNER_TYPES: Tuple[str, ...] = (
    "HorizontalWallTopLeft",
    "HorizontalWallTopRight",
    "HorizontalWallBottomLeft",
    "HorizontalWallBottomRight",
)

FLOOR_RULES: Tuple[ConnectRuleSpec, ...] = tuple(
    ConnectRuleSpec(f, w) for f in FLOOR_CORNER_TYPES for w in WALL_CORNER_TYPES
)

# 全量：墙族 + 地板角
ALL_RULES: Tuple[ConnectRuleSpec, ...] = WALL_FAMILY_RULES + FLOOR_RULES


# =====================================================================
# 安装
# =====================================================================
def _make_observer(log: Optional[list]) -> Callable[[Any, Any], None]:
    """观测回调：只在给了 log 时记录一条，不参与任何逻辑。"""
    def cb(a, b):
        if log is not None:
            log.append(f"{a.short()} \u2194 {b.short()}")
    return cb


def install(rules: Sequence[ConnectRuleSpec] = ALL_RULES, *,
            log: Optional[list] = None,
            registry: Any = None) -> List[Any]:
    """把声明装进规则表，返回实际生效的 ConnectRule 列表。

    **幂等**：已存在的类型对直接复用，不重复注册、不抛错。这条语义必须保留：
    同一张全局规则表会被多个房子/场景反复组装（RectangleHouse._ensure_rules），
    重复注册会命中 port.RuleRegistry 的"一份规则写一遍"保护而炸掉。

    注意既有的一个副作用（刻意保留、没有偷偷改）：规则已存在时返回的是**先前那条**
    规则，它绑的是**先前那次调用**的 log —— 所以第二个调用方的 log 收不到事件。
    """
    from src.core.build_game.port import RULES, register_rule

    reg = registry if registry is not None else RULES
    out: List[Any] = []
    for spec in rules:
        existing = reg.get(spec.type_a, spec.type_b)
        if existing is not None:
            out.append(existing)
            continue
        out.append(register_rule(spec.type_a, spec.type_b,
                                 registry=reg,
                                 action=spec.action,
                                 on_connect=_make_observer(log)))
    return out


def rule_keys(rules: Sequence[ConnectRuleSpec] = ALL_RULES) -> List[Tuple[str, str]]:
    """声明表的键（供自省/比对/文档）。"""
    return [r.key for r in rules]


def describe(rules: Sequence[ConnectRuleSpec] = ALL_RULES) -> str:
    """人读的规则清单。"""
    lines = [f"{len(rules)} 条连接规则："]
    for r in rules:
        lines.append(f"  {r.type_a:26s} <-> {r.type_b:26s}  action={r.action}")
    return "\n".join(lines)
