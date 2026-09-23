"""wallkit.py —— 墙体规则库的对外门面（目标2）。

## 用法：像写 pygame 一样短

    from src.core.build_game import wallkit as wk

    wall   = wk.vwall(5, 3, at=(300, 400))                       # 竖墙
    front  = wk.hwall(6, 3, at=(900, 1250), overlap_bottom=True) # 前墙（下角可重叠口）
    ground = wk.floor(6, 4, at=(900, 1250))                      # 地板
    house  = wk.house(6, 4, 3, at=(900, 1250))                   # 整栋房子
    link   = wk.hall(2, 4, 3, at=(900, 1900), orientation="ns")  # 走廊
    wk.edge(house, "south", [link])                              # 南边拆开接走廊 + 补墙

    world.add_game_object(wall, "structures")

## 规则住在库里，不住在调用点

    wall_spec.py          行 → 段语义        （生成规则）
    vertical_texture.py   段+连接态 → 贴图    （绘制规则）
    wall_grid.py          格子定位/深度       （绘制规则）
    port.py              谁能连谁            （连接规则）
    Material             贴图来源 + 精灵类

## 两条默认原则（贯穿全库）

  - **纯几何自动推导是默认**：不声明任何规则时，画的就是引擎自动算出来的样子
    （`side_face=None`、`occlusion=NONE`）。
  - **显式规则是覆写**：需要时在调用点声明，例如
    `wk.vwall(5, 3, side_face="49", occlusion="cutaway")`。

两者互不干扰：不写 = 零行为变化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from src.core.build_game.wall_spec import (
    Band, WallSpec, VWALL_SPEC, HWALL_SPEC, FLOOR_SPEC, SPECS, spec_named,
)
from src.core.build_game.wall_rules import (
    ConnectRuleSpec, WALL_FAMILY_RULES, FLOOR_RULES, ALL_RULES,
    install as install_rules, describe as describe_rules,
)
from src.core.build_game.vertical_texture import (
    vertical_segment, vertical_cell_frame, _TABLE as TEXTURE_TABLE,
)

__all__ = [
    # 构造门面
    "vwall", "hwall", "floor", "house", "hall", "edge",
    # 材质
    "Material", "WOOD", "register_material", "material_named",
    # 生成规则（行 → 段语义）
    "Band", "WallSpec", "VWALL_SPEC", "HWALL_SPEC", "FLOOR_SPEC",
    "SPECS", "spec_named",
    # 连接规则（谁能连谁）
    "ConnectRuleSpec", "WALL_FAMILY_RULES", "FLOOR_RULES", "ALL_RULES",
    "install_rules", "describe_rules",
    # 绘制规则（段+连接态+墙侧 → 贴图）
    "TEXTURE_TABLE", "vertical_segment", "vertical_cell_frame",
]


# =====================================================================
# 材质：一套"贴图来源 + 精灵类"。换材质 = 换这个对象，其余规则不动。
# =====================================================================
@dataclass(frozen=True)
class Material:
    """一种墙体材质的组装信息。

    - vwall_cls / hwall_cls / floor_cls : 具体 Structure 类（自带精灵类表与贴图加载）
    - name                              : 观测用标签
    """

    name: str
    vwall_cls: Any = None
    hwall_cls: Any = None
    floor_cls: Any = None

    def __repr__(self) -> str:
        return f"Material({self.name!r})"


def _make_wood() -> Material:
    """木墙材质：类在 import 期就要拿到（贴图本身由各类延迟加载）。"""
    from src.core.build_game.verticalwall import VerticalWoodWall
    from src.core.build_game.horizontalwall import HorizontalWoodWall
    from src.core.build_game.floor import BrickFloor

    return Material(name="wood", vwall_cls=VerticalWoodWall,
                    hwall_cls=HorizontalWoodWall, floor_cls=BrickFloor)


WOOD = _make_wood()


def register_material(material: Material) -> Material:
    """把新材质登记进库（供后续按名字取用）。"""
    _MATERIALS[material.name] = material
    return material


def material_named(name: str) -> Optional[Material]:
    return _MATERIALS.get(name)


_MATERIALS = {WOOD.name: WOOD}


# =====================================================================
# 遮挡策略：字符串写起来短，内部仍是 OcclusionPolicy
# =====================================================================
def _apply_occlusion(obj, occlusion) -> None:
    if occlusion is None:
        return                                  # 不声明 = 保持默认 NONE
    from src.utils.layer_v2 import OcclusionPolicy
    if isinstance(occlusion, OcclusionPolicy):
        obj.occlusion_policy = occlusion
    else:
        obj.occlusion_policy = OcclusionPolicy(str(occlusion).strip().lower())


# =====================================================================
# 构造门面
# =====================================================================
def vwall(length: int, height: int, *,
          at: Tuple[float, float] = (0, 0),
          material: Optional[Material] = None,
          side_face: Any = None,
          side_fill: float = 1.0,
          occlusion: Any = None):
    """竖墙：宽恒 1 列；逻辑尺寸 = 长 × 高。

    side_face  : None（默认，纯几何=半格宽）或 House 编号（如 "49"）/ Surface
    side_fill  : 侧面占"空半格"的比例，1.0 填满、0.5 只宽一点
    occlusion  : None（默认 NONE）或 "cutaway"/"fade"/"outline"
    """
    m = material or WOOD
    if m.vwall_cls is None:
        raise ValueError(f"材质 {m.name!r} 没有竖墙实现")
    obj = m.vwall_cls(int(length), int(height), center=tuple(at))
    if side_face is not None:
        obj.set_side_face(side_face, side_fill)
    _apply_occlusion(obj, occlusion)
    return obj


def hwall(width: int, height: int, *,
          at: Tuple[float, float] = (0, 0),
          material: Optional[Material] = None,
          overlap_bottom: bool = False,
          occlusion: Any = None):
    """横墙：逻辑尺寸 = 列数 × 行数。

    overlap_bottom=True 时下两角挂"可重叠"端口（供底墙与地板下角重叠连接）。
    """
    m = material or WOOD
    if m.hwall_cls is None:
        raise ValueError(f"材质 {m.name!r} 没有横墙实现")
    obj = m.hwall_cls((int(width), int(height)), tuple(at),
                      overlap_bottom=bool(overlap_bottom))
    _apply_occlusion(obj, occlusion)
    return obj


def floor(width: int, depth: int, *,
          at: Tuple[float, float] = (0, 0),
          material: Optional[Material] = None):
    """地板：逻辑尺寸 = 列数 × 行数（水平地面，整块画在站在它上面的东西之前）。"""
    m = material or WOOD
    if m.floor_cls is None:
        raise ValueError(f"材质 {m.name!r} 没有地板实现")
    return m.floor_cls((int(width), int(depth)), tuple(at))


def house(length: int, depth: int, height: int, *,
          at: Tuple[float, float] = (0, 0),
          material: Optional[Material] = None,
          cell: Tuple[float, float] = (64, 64),
          occlusion: Any = None,
          side_face: Any = None,
          side_fill: float = 1.0):
    """整栋房子：地板 + 四墙，端口自动装配。返回 House（自遮挡组合结构）。

    side_face 会转发给各部件，实际生效的是左右两道竖墙（横墙/地板本就满格，自动跳过）。

    目前只支持 wood 材质：装配走 RectangleHouse，它的部件工厂直接写死了木纹类。
    要换材质，得先给 RectangleHouse 接上"部件工厂"参数 —— 在那之前这里显式报错，
    而不是悄悄用木纹件拼出一栋看起来对、其实材质错了的房子。
    """
    m = material or WOOD
    if m.name != "wood":
        raise ValueError(
            f"house 目前只支持 wood 材质（RectangleHouse 的部件工厂写死了木纹类）；"
            f"换材质需要先给它接上部件工厂。收到材质 {m.name!r}。")
    from src.core.build_game.house import House as _House
    obj = _House.build(int(length), int(depth), int(height),
                       cell=tuple(cell), center=tuple(at), use_wood=True)
    _apply_occlusion(obj, occlusion)
    if side_face is not None:
        obj.set_side_face(side_face, side_fill)
    return obj


def hall(length: int, depth: int, height: int, *,
         at: Tuple[float, float] = (0, 0),
         orientation: str = "ns",
         material: Optional[Material] = None,
         occlusion: Any = None,
         side_face: Any = None,
         side_fill: float = 1.0):
    """走廊（Hall）：地板 + 一个轴向上的两道墙，另一个轴向两端开口。

    orientation="ns"（南北向：竖墙在左右、南北开口）/ "ew"（东西向：横墙在上下、
    东西开口）。两端各挂一个【开口端面】端口：可以走廊串走廊，也可以接到房子被
    拆开的那条边上（见 :func:`edge`）。

    接上之后，被接那一端的墙头会自动切成"跑通"纹（不再画自由端收口）——
    两段墙看起来是一整堵。
    """
    m = material or WOOD
    if m.name != "wood":
        raise ValueError(
            f"hall 目前只支持 wood 材质（Hall.build 的部件工厂写死了木纹类）；"
            f"收到材质 {m.name!r}。")
    from src.core.build_game.hall import Hall as _Hall
    obj = _Hall.build(int(length), int(depth), int(height),
                      center=tuple(at), use_wood=True, orientation=str(orientation))
    _apply_occlusion(obj, occlusion)
    if side_face is not None:
        obj.set_side_face(side_face, side_fill)
    return obj


def edge(house_obj, side: str, halls=(), *,
         fill_gaps: bool = True,
         geometry: str = "exact",
         log=None):
    """把房子的一条边【拆开】接若干走廊，并把剩下的缝补成小墙。

        edge(house, "south", [hall_a, hall_b])          # 自动排布 + 自动补墙
        edge(house, "south", [(hall_a, 0), (hall_b, 4)])# 指定每条走廊占的第一格

    返回 {"edge": HouseEdge, "halls": [...], "segments": [...], "log": [...]}。
    细节（占格、补墙、端口规则）见 house_edge 模块。
    """
    from src.core.build_game.house_edge import connect_halls_to_house
    return connect_halls_to_house(house_obj, side, list(halls),
                                  fill_gaps=bool(fill_gaps), geometry=str(geometry),
                                  log=log)
