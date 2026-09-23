"""wall_spec.py —— 墙体"行 → 段语义"的声明式描述（目标2 规则库的第一块砖）。

## 为什么要它

同一套行分区原来被写了【两遍】、分开维护，天然会漂移：

    VerticalWall._map()           逐行产出 {"rows": (a,b), "type": seg} 给注册精灵用
    VerticalWall._cell_type_at()  逐行回答"这一行是什么段"

两者编码的完全是同一件事（横墙只有前者）。本模块把它收成**一份声明**，
两边都从它推导 —— 改一个地方，两边同时生效。

## 怎么读

一个 WallSpec 描述了：
  - param_names : 这两个尺寸参数叫什么（仅作文档/自省，便于核对）
  - grid_size   : (参数) -> (列数, 行数)  —— 逻辑网格
  - bands       : 若干 Band，每个 Band 是【左闭右开】的连续行区间 + 段语义名

Band 的区间由 (a, b) 两个尺寸参数算出；区间为空则自动跳过。
段语义名同时就是 sprite_types 的 key，也是 _cell_type_at 的返回值 —— 一个名字走通三处。

## 加新墙型

不用写新类，声明一个 WallSpec 即可：给 grid_size 和 bands，段名对上 sprite_types，
再把 spec 挂到类的 WALL_SPEC 上，_map()/_cell_type_at() 自动就对了。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

RangeFn = Callable[[int, int], Tuple[int, int]]


@dataclass(frozen=True)
class Band:
    """一段连续行（左闭右开 [start, stop)）与它的段语义名。

    range_for(a, b) 返回该段在当前尺寸下占的行区间；start >= stop 表示这段
    在这个尺寸下不存在（例如只有 2 行高的竖墙没有"中段"），会被自动跳过。
    """

    segment: str
    range_for: RangeFn

    def rows(self, a: int, b: int) -> Tuple[int, int]:
        start, stop = self.range_for(int(a), int(b))
        return int(start), int(stop)


@dataclass(frozen=True)
class WallSpec:
    """一种墙的"行 → 段语义"声明。"""

    name: str
    param_names: Tuple[str, str]
    grid_size: Callable[[int, int], Tuple[int, int]]
    bands: Tuple[Band, ...]
    default_segment: Optional[str] = None   # segment_at 落空时的兜底（正常不该发生）

    # ---- 网格 ----
    def grid(self, a: int, b: int) -> Tuple[int, int]:
        gw, gh = self.grid_size(int(a), int(b))
        return int(gw), int(gh)

    # ---- 段语义：逐行回答"这一行是什么段" ----
    def segment_at(self, gy: int, a: int, b: int) -> Optional[str]:
        """行号 -> 段语义名。区间是左闭右开、且互不重叠，取第一个命中的。"""
        gy = int(gy)
        for band in self.bands:
            start, stop = band.rows(a, b)
            if start <= gy < stop:
                return band.segment
        return self.default_segment

    # ---- 注册规则：与旧 _map() 输出【同形】 ----
    def rules(self, a: int, b: int) -> List[dict]:
        """-> [{"rows": (start, stop), "type": seg}, ...]

        与旧实现的唯一差别是"空区间自动省略"—— 而旧实现里的 L>=3 / L>=2 /
        side_end-1>L 这几个条件，恰好就等价于"该区间非空"，因此输出逐条相同
        （见 tests/test_wall_spec.py 的穷举比对）。
        """
        out: List[dict] = []
        for band in self.bands:
            start, stop = band.rows(a, b)
            if stop > start:
                out.append({"rows": (start, stop), "type": band.segment})
        return out

    # ---- 自省：给规则库/调试用 ----
    def bands_at(self, a: int, b: int) -> List[Tuple[str, int, int]]:
        """当前尺寸下实际存在的 (段名, start, stop)。"""
        return [(bd.segment, *bd.rows(a, b)) for bd in self.bands
                if bd.rows(a, b)[1] > bd.rows(a, b)[0]]

    def __repr__(self) -> str:
        segs = "|".join(bd.segment for bd in self.bands)
        return f"WallSpec({self.name!r}, params={self.param_names}, bands={segs})"


# =====================================================================
# 竖墙：宽恒 1 列，逻辑尺寸 = 长(L) × 高(H)
#   屏幕网格 = 1 列 × (L + H - 1) 行
#   前 L 行是"沿地面铺开的横段"（头/中/尾），后 H-1 行是南端的"侧立面"
# =====================================================================
VWALL_SPEC = WallSpec(
    name="vertical_wall",
    param_names=("L(长)", "H(高)"),
    grid_size=lambda L, H: (1, L + H - 1),
    bands=(
        Band("top",      lambda L, H: (0, 1)),          # 头
        Band("top_run",  lambda L, H: (1, L - 1)),      # 中段（沿地面）
        Band("top_tail", lambda L, H: (L - 1, L)),      # 尾段（横段末端，墙脚所在）
        Band("middle",   lambda L, H: (L, L + H - 2)),  # 侧立面中段
        Band("bottom",   lambda L, H: (L + H - 2, L + H - 1)),   # 侧立面底段
    ),
)


# =====================================================================
# 横墙：逻辑尺寸 = (gw 列, gh 行)，屏幕网格与逻辑网格一致
#   第 0 行 = 顶，末行 = 底，中间全是中段
# =====================================================================
HWALL_SPEC = WallSpec(
    name="horizontal_wall",
    param_names=("gw(列)", "gh(行)"),
    grid_size=lambda gw, gh: (gw, gh),
    bands=(
        Band("top",    lambda gw, gh: (0, 1)),
        Band("middle", lambda gw, gh: (1, gh - 1)),
        Band("bottom", lambda gw, gh: (gh - 1, gh)),
    ),
)


# 地板：整块一种砖瓦，一段到底
FLOOR_SPEC = WallSpec(
    name="floor",
    param_names=("gw(列)", "gh(行)"),
    grid_size=lambda gw, gh: (gw, gh),
    bands=(
        Band("floor", lambda gw, gh: (0, gh)),
    ),
)

SPECS = {
    VWALL_SPEC.name: VWALL_SPEC,
    HWALL_SPEC.name: HWALL_SPEC,
    FLOOR_SPEC.name: FLOOR_SPEC,
}


def spec_named(name: str) -> Optional[WallSpec]:
    return SPECS.get(name)
