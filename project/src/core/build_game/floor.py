"""floor.py —— 砖块地板（Floor / BrickFloor）(S4：拆继承后)

地板与横墙/竖墙同属“墙体族”的网格结构，但目的不同：
- 横墙/竖墙是【竖直立面】，同族四角端口词汇一组；
- 地板是【水平砖面】，用作铺地纹理，同时四角各带一个「特殊端口」。

因此地板直接继承 Structure 并混入 WallGridMixin —— 复用“格子网格”机制
（把 cell 精灵注册进 shape.get_space()、按中心+格尺寸摆放、解析行映射、按深度排序），
并在四角声明 FloorCornerPort（特殊口，见下）。

纹理：单一种类 —— 整块地板每一格都用同一张砖纹瓦片 House_17。

特殊角口（FloorCornerPort）：
- 既能连横墙、也能连竖墙（register_floor_corner_rules 与墙族四角词汇互通）；
- max_connections = 2：一个角口最多挂 2 个连接；
- 标记状态：绿=空闲 / 黄=已连 1 个未满 / 红=连满(占用)。
"""

import pygame

from src.core.build_game.structure import Structure, RenderOrder
from core.build_game.grid.wall_grid import WallGridMixin
from src.core.build_game.tiledshape import TiledRect
from src.core.build_game.port import Port, OverlapMixinPort
from src.core.build_game.wall_spec import FLOOR_SPEC, WallSpec


class FloorCornerPort(Port):
    """地板四角「特殊端口」：既能连横墙、也能连竖墙，且最多可连 2 个对端。

    与普通墙口(横墙/竖墙四角端口)不同处：
    - max_connections = 2：一个角口允许挂最多 2 个连接；
    - 连 1 个仍未满(黄) → 连满 2 个才视为占用(红)；
    - 候选能力由 register_floor_corner_rules 注册到全局规则表 —— 能与
      HorizontalWall 的四种角端口（横/竖墙复用的同一族四角词汇）互连接。
    """

    port_type = "FloorCorner"
    max_connections = 2
    connectable_sides = ("top", "bottom", "left", "right")


class FloorCornerBottomPort(OverlapMixinPort):
    """地板下两角「特殊重叠端口」：可与横墙下角、竖墙底口重叠连接。

    与上方角口 FloorCornerPort 不同处：
    - 继承 OverlapMixinPort：当与对端走【同一边】连接（如 bottom↔bottom）时，
      触发“完全重叠”吸附（两格中心重合），而非互补贴靠；
    - max_connections = 2：一个下角口仍可挂最多 2 个连接
      （竖墙.bottom 占一侧、底横墙下角经 bottom↔bottom 重叠占另一侧）。
    """

    port_type = "FloorCornerBottom"
    max_connections = 2
    connectable_sides = ("top", "bottom", "left", "right")


class Floor(WallGridMixin, Structure):
    """砖块地板（无纹理壳）。逻辑网格 = 宽(W) × 高(H) 格。

    - shape = TiledRect（矩形格空间）；网格尺寸/格尺寸/中心由子类或构造传入。
    - 精灵种类只有一种："floor" —— 所有格共用同一张砖纹瓦片。
    - 连接能力：四个角的逻辑格各带一个 FloorCornerPort（特殊口，见上）。
    """

    structure_type = "floor"

    # 行 → 段语义的声明（见 wall_spec.py）：整块地板一段到底
    WALL_SPEC: WallSpec = FLOOR_SPEC

    def __init__(self,
                 grid_size: tuple[int, int],
                 cell_size: tuple[int | float, int | float] = (64, 64),
                 center: tuple[float, float] = (0, 0),
                 layer_offset: int = RenderOrder.BOTTOM.value,
                 shape_cls=None):
        super().__init__(layer_offset)
        self._grid_width, self._grid_height = grid_size
        self._cell_w, self._cell_h = cell_size
        if shape_cls is None:
            self.shape = TiledRect(grid_size, center, allow_1xN=True)
        else:
            self.shape = shape_cls(grid_size, center, allow_1xN=True)
        self.sprite_types = {
            "floor": pygame.sprite.Sprite,
        }

    def _map(self):
        """整块地板自始至终一种砖瓦：同样由 WALL_SPEC 声明推导。"""
        return self.WALL_SPEC.rules(self._grid_width, self._grid_height)

    def depth_base_y(self) -> float:
        """地板基准 = 自身【最北】那条边（再下移 1px）。

        地板是【水平地面】，不是立面：整块地板必须画在一切"站在它上面"的东西之前，
        否则站在屋内偏南的角色会被自己脚下的地砖盖住。取最北边（而不是南边/中心）
        是因为地板上的实体脚底 Y 只会 ≥ 北边，取北边即保证它们全部排在地板之后。
        """
        _, top = self._get_top_left()
        return float(top) - 1.0

    def cell_depth_base_y(self, grid_pos) -> float:
        """地板整块共用同一条基准线（见 depth_base_y）：地面不分格抢深度。"""
        return self.depth_base_y()

    def port_node_for_cell(self, cell: tuple[int, int]):
        """四角各带一个特殊角 Port：上两角=普通角口，下两角=可重叠角口；其余边界格无端口。"""
        gw, gh = self._grid_width, self._grid_height
        corner_map = {
            (0, 0): FloorCornerPort,
            (gw - 1, 0): FloorCornerPort,
            (0, gh - 1): FloorCornerBottomPort,
            (gw - 1, gh - 1): FloorCornerBottomPort,
        }
        return corner_map.get(cell)

    def port_name_for(self, side: str, index: int, cell: tuple[int, int]):
        """四角语义名：top_left / top_right / bottom_left / bottom_right。"""
        gw, gh = self._grid_width, self._grid_height
        name_map = {
            (0, 0): "top_left",
            (gw - 1, 0): "top_right",
            (0, gh - 1): "bottom_left",
            (gw - 1, gh - 1): "bottom_right",
        }
        return name_map.get(cell)

    def on_topology_settled(self):
        """地板无立面连接拓扑，无需重算纹理。"""
        return None


class BrickFloor(Floor):
    """砖块地板（具体）：签名 (格数, center)。精灵类在 entity 定义，全部用 House_17 砖纹。"""

    def __init__(self, grid_size: tuple[int, int], center=(0, 0)):
        Floor.__init__(self, grid_size, (64, 64), center)
        from src.game.entity import BrickFloorTile
        self.sprite_types["floor"] = BrickFloorTile
        self.parse_and_register(config=self._map(), sprite_factory=self.sprite_factory)

    def sprite_factory(self, typ):
        return self.sprite_types[typ]()


def register_floor_corner_rules(log=None):
    """注册地板四角特殊口的连接规则 —— 委托给 wall_rules 的声明表。

    规则本身写在 wall_rules.FLOOR_RULES（FloorCorner/FloorCornerBottom × 墙族四角），
    本函数只负责"装上"。幂等：已存在的类型对直接复用。

    为什么一份表就够：竖墙复用横墙这一族四角端口词汇（见 VerticalWallPort.edge_type），
    所以"地板角 × 墙族四角"同时覆盖了地板连横墙与地板连竖墙两种情况。
    """
    from src.core.build_game.wall_rules import FLOOR_RULES, install
    return install(FLOOR_RULES, log=log)
