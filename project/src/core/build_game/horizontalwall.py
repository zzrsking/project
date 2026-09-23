import pygame

from src.core.build_game.structure import Structure, RenderOrder
from core.build_game.grid.wall_grid import WallGridMixin
from src.core.build_game.tiledshape import TiledRect
from src.core.build_game.port import Port, OverlapMixinPort
from src.core.build_game.wall_spec import HWALL_SPEC, WallSpec


# ================= HorizontalWall 的四种角 Port（子类即类型 / 节点边接口） =================
# 横墙角口：一个格子上同时挂着【两个不同角色】的方向 —— 例如下左角 = 向下连地板
# （bottom）+ 向左连邻墙（left）。容量取方向数（=2），两个角色各占一条边、互不挤占。
#
# 为什么不能再压成 1：房子/走廊的墙在自身装配时就用掉了一条边（横墙下角 ↔ 地板），
# 于是那条边上再来的邻居（走廊拆边后补出来的竖墙段）永远连不上 —— 表现就是
# "竖墙没有对应的连接口，纹理停在自由端"。
# 代价：只连了一边的角口底色从"红(已满)"变成"黄(未满)"，连满两条边才是红。
class HorizontalWallTopLeftPort(Port):
    """横墙左上角节点：可从 top / left 方向被连接。"""
    port_type = "HorizontalWallTopLeft"
    connectable_sides = ("top", "left")


class HorizontalWallTopRightPort(Port):
    """横墙右上角节点：可从 top / right 方向被连接。"""
    port_type = "HorizontalWallTopRight"
    connectable_sides = ("top", "right")


class HorizontalWallBottomLeftPort(Port):
    """横墙左下角节点：可从 bottom / left 方向被连接。"""
    port_type = "HorizontalWallBottomLeft"
    connectable_sides = ("bottom", "left")


class HorizontalWallBottomRightPort(Port):
    """横墙右下角节点：可从 bottom / right 方向被连接。"""
    port_type = "HorizontalWallBottomRight"
    connectable_sides = ("bottom", "right")


# 横墙下角「可重叠」端口：当与对端走同一边（如 bottom↔bottom）连接时，
# 触发完全重叠吸附（两格中心重合），而非互补贴靠。类型沿用墙族四角词汇，
# 规则一份即可复用；仅当 HorizontalWall(overlap_bottom=True) 时被挂到下两角。
class HorizontalWallBottomLeftOverlapPort(OverlapMixinPort):
    """横墙下左角可重叠端口：底边可重叠连接。"""
    port_type = "HorizontalWallBottomLeft"
    connectable_sides = ("bottom", "left")


class HorizontalWallBottomRightOverlapPort(OverlapMixinPort):
    """横墙下右角可重叠端口：底边可重叠连接。"""
    port_type = "HorizontalWallBottomRight"
    connectable_sides = ("bottom", "right")


# ============== 四角"角色"声明（端口选型与命名的唯一来源） ==============
# 一格能承担哪些角角色，完全由【网格尺寸 + 格坐标】决定。正常墙（gw>=2 且 gh>=2）
# 每格恰好一个角色，选型与命名与原来逐字相同；尺寸退化成 1 时多个角色落到同一格，
# 才需要合并端口（见下面的 Degenerate 类）。
CORNER_ROLES = ("top_left", "top_right", "bottom_left", "bottom_right")

_ROLE_SIDES = {
    "top_left":     ("top", "left"),
    "top_right":    ("top", "right"),
    "bottom_left":  ("bottom", "left"),
    "bottom_right": ("bottom", "right"),
}

# 角色 -> 规则表里的角词汇（与上面四个 port_type 字符串完全一致）
_ROLE_EDGE_TYPE = {
    "top_left":     "HorizontalWallTopLeft",
    "top_right":    "HorizontalWallTopRight",
    "bottom_left":  "HorizontalWallBottomLeft",
    "bottom_right": "HorizontalWallBottomRight",
}

# 角色 -> 单角色时用的端口类（保持原有四个角端口类不变）
_ROLE_PORT_CLASS = {
    "top_left":     HorizontalWallTopLeftPort,
    "top_right":    HorizontalWallTopRightPort,
    "bottom_left":  HorizontalWallBottomLeftPort,
    "bottom_right": HorizontalWallBottomRightPort,
}

# 下角且 overlap_bottom=True 时改用"可重叠"版（与原来的选择完全一致）
_ROLE_OVERLAP_PORT_CLASS = {
    "bottom_left":  HorizontalWallBottomLeftOverlapPort,
    "bottom_right": HorizontalWallBottomRightOverlapPort,
}


def corner_roles_of(wall, cell) -> tuple:
    """某一格承担的全部角角色（按 CORNER_ROLES 顺序）。

    正常墙每格一个；gw==1 时 (0,0) 同时是左上与右上；gh==1 时同一格既是上角
    又是下角；gw==1 且 gh==1 时四个角色全落在同一格上。
    """
    gw, gh = int(wall._grid_width), int(wall._grid_height)
    gx, gy = int(cell[0]), int(cell[1])
    roles = []
    if gy == 0:
        if gx == 0:
            roles.append("top_left")
        if gx == gw - 1:
            roles.append("top_right")
    if gy == gh - 1:
        if gx == 0:
            roles.append("bottom_left")
        if gx == gw - 1:
            roles.append("bottom_right")
    return tuple(roles)


class _DegenerateCornerMixin:
    """退化角口的共用逻辑（不继承 Port，避免两个 Port 子类多继承的布局冲突）。

    具体类由下面两个各自单继承一条链：
        HorizontalWallDegenerateCornerPort        = (Mixin, Port)
        HorizontalWallDegenerateCornerOverlapPort = (Mixin, OverlapMixinPort)
    """

    port_type = "HorizontalWallTopLeft"      # 观测用缺省；查规则一律走 edge_type(side)
    connectable_sides = ("top", "left", "right")
    max_connections = 2

    def __init__(self, structure, index, cell,
                 max_connections=None, connectable_sides=None):
        # 端口由 _node_port_for_cell 以 (structure, None, cell) 构造，
        # 所以角色只能从宿主墙的网格尺寸 + 本格坐标现算。
        self.roles = corner_roles_of(structure, cell)
        sides = []
        for role in self.roles:
            for s in _ROLE_SIDES[role]:
                if s not in sides:
                    sides.append(s)
        super().__init__(structure, index, cell,
                         max_connections=max(1, len(self.roles)),
                         connectable_sides=tuple(sides))

    def edge_type(self, side: str) -> str:
        """按方向分派角词汇：左→TopLeft/BottomLeft，右→TopRight/BottomRight。

        top / bottom 在塌缩格上是多义的（同一格既是左角又是右角），这里取
        "靠左的那个"（与原来"先登记的 top_left 生效"的行为一致）。
        """
        gx, gy = int(self.cell[0]), int(self.cell[1])
        if side == "right":
            return _ROLE_EDGE_TYPE["top_right" if gy == 0 else "bottom_right"]
        if side == "bottom":
            return _ROLE_EDGE_TYPE["bottom_left" if gx == 0 else "bottom_right"]
        return _ROLE_EDGE_TYPE["top_left" if gy == 0 else "bottom_left"]

    def describe(self):
        return (f"DegenerateCorner{list(self.roles)} cell={self.cell} "
                f"sides={list(self.connectable_sides)} "
                f"struct={type(self.structure).__name__}")


class HorizontalWallDegenerateCornerPort(_DegenerateCornerMixin, Port):
    """横墙【多角塌缩格】端口：宽=1（或高=1）时几个角名落到同一格上。

    为什么必须有它：横墙的角口原来按"格坐标作 key"登记（`corner_map[cell] = cls`），
    gw==1 时 `(0,0)` 既是左上角又是右上角，**后写的把先写的覆盖掉** —— 于是这条
    1 宽墙段只剩朝右的角口：它左边的邻居（走廊侧墙）永远连不上，而右边那个即使
    连上也是"边类型错位"（拿 TopRight 的词汇去配左边的几何），画出来是 22 + 占位 31。

    本类把"这一格承担的全部角角色"合成一个节点：

    · `connectable_sides` = 所有角色方向的并集（宽=1 的顶格 → top/left/right，
      也就是**左右都能连**；高=1 的左格 → top/bottom/left）；
    · `max_connections`   = 角色个数（左右各一个连接互不挤占）；
    · `edge_type(side)`   = 按方向分派回【既有的四个角词汇】—— 规则表里
      (TopLeft, TopRight) 这些对本来就已经注册，**不需要新增任何规则**。
    """


class HorizontalWallDegenerateCornerOverlapPort(_DegenerateCornerMixin, OverlapMixinPort):
    """退化角口 + 可重叠：该格含下角角色、且墙 overlap_bottom=True 时用它。

    MRO 里 `_snap_self_to_peer` 落到 OverlapMixinPort —— bottom↔bottom
    （补墙段 ↔ 房子边缘格）才会走"格心完全重叠"，与原来一致。
    """

    port_type = "HorizontalWallBottomLeft"


class HorizontalWall(WallGridMixin, Structure):
    """水平墙体：由网格单元组成。直接继承 Structure，混入 WallGridMixin 获得格子机制。

    - shape = TiledRect（矩形格空间）；网格尺寸/格尺寸/中心由子类或构造传入。
    - 连接能力：四个角的逻辑格各带一个不同 Port（见上方四角 Port 子类）。
    """

    structure_type = "horizontal_wall"

    # 行 → 段语义的声明（见 wall_spec.py）
    WALL_SPEC: WallSpec = HWALL_SPEC

    def __init__(self,
                 grid_size: tuple[int, int],
                 cell_size: tuple[int | float, int | float],
                 center: tuple[float, float] = (0, 0),
                 layer_offset: int = RenderOrder.MIDDLE.value,
                 allow_1xN: bool = False,
                 overlap_bottom: bool = False,
                 shape_cls=None):
        super().__init__(layer_offset)
        self._grid_width, self._grid_height = grid_size
        self._cell_w, self._cell_h = cell_size
        self.overlap_bottom = bool(overlap_bottom)
        if shape_cls is None:
            self.shape = TiledRect(grid_size, center, allow_1xN=allow_1xN)
        else:
            self.shape = shape_cls(grid_size, center, allow_1xN=allow_1xN)
        self.sprite_types = {
            "top": pygame.sprite.Sprite,
            "bottom": pygame.sprite.Sprite,
            "middle": pygame.sprite.Sprite,
        }

    def _map(self):
        """行映射：由 WALL_SPEC 声明推导（段名同时就是 sprite_types 的 key）。"""
        return self.WALL_SPEC.rules(self._grid_width, self._grid_height)

    def port_node_for_cell(self, cell: tuple[int, int]):
        """该格承担的角角色决定挂哪个角 Port 节点；非角格无端口。

        · 一格一个角色（正常墙）→ 仍用原来的四角端口类，行为不变；
          overlap_bottom=True 时下角用「可重叠」版（下角与地板角格心重叠）。
        · 一格多个角色（gw==1 / gh==1 的塌缩格）→ 用 Degenerate 端口，
          它的 connectable_sides 是这些角色方向的并集 —— **左右都能连**。
        """
        roles = corner_roles_of(self, cell)
        if not roles:
            return None
        if len(roles) == 1:
            role = roles[0]
            if self.overlap_bottom and role in _ROLE_OVERLAP_PORT_CLASS:
                return _ROLE_OVERLAP_PORT_CLASS[role]
            return _ROLE_PORT_CLASS[role]
        has_bottom = any(r in ("bottom_left", "bottom_right") for r in roles)
        if self.overlap_bottom and has_bottom:
            return HorizontalWallDegenerateCornerOverlapPort
        return HorizontalWallDegenerateCornerPort

    def port_name_for(self, side: str, index: int, cell: tuple[int, int]):
        """四角语义名：top_left / top_right / bottom_left / bottom_right。

        正常墙一格一个名字（与原实现逐字一致）。塌缩格上多个名字指向**同一个**
        端口实例，所以这里必须按 `side` 决定"这次用哪个名字"——否则 `ports`
        里只会剩下先登记的那一个，`top_left` 或 `top_right` 又会缺一个。
        """
        roles = corner_roles_of(self, cell)
        if not roles:
            return None
        if len(roles) == 1:
            return roles[0]
        gx, gy = int(cell[0]), int(cell[1])
        if side == "right":
            return "top_right" if gy == 0 else "bottom_right"
        if side == "bottom":
            return "bottom_left" if gx == 0 else "bottom_right"
        return "top_left" if gy == 0 else "bottom_left"


class HorizontalWoodWall(HorizontalWall):
    def __init__(self, grid_size, center, overlap_bottom=False, allow_1xN=False):
        super().__init__(grid_size, (64, 64), center, overlap_bottom=overlap_bottom,
                         allow_1xN=allow_1xN)
        # 延迟导入实体类：避免无显示环境下 import 本模块就拉起完整资源链
        from src.game.entity import (HorizontalWoodWallTiledTop,
                                     HorizontalWoodWallTiledBottom,
                                     HorizontalWoodWallTiledMiddle)
        self.sprite_types["top"] = HorizontalWoodWallTiledTop
        self.sprite_types["bottom"] = HorizontalWoodWallTiledBottom
        self.sprite_types["middle"] = HorizontalWoodWallTiledMiddle
        self.parse_and_register(config=self._map(), sprite_factory=self.sprite_factory)

    def sprite_factory(self, typ):
        return self.sprite_types[typ]()
