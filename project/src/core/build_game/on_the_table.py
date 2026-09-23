"""on_the_table.py —— 桌子（双层网格结构）的具体实现。

## 这是什么

一张桌子 = 一个 `Structure`，内部有**两层格子**（见 `grid/table_grid.py`）：
  - `table`  层：桌面本体（板面 / 桌腿 / 桌面装饰）；
  - `object` 层：摆在桌面上的东西，每格默认只放一个。

## 两个必须守住的写法（否则“能构造但什么都不能做”）

1. **继承顺序：mixin 在前。**
   `class Table(TableGridMixin, Structure)` —— 与 `HorizontalWall(WallGridMixin, Structure)`、
   `Floor(WallGridMixin, Structure)` 同款约定。
   写成 `(Structure, TableGridMixin)` 时 MRO 是
   `Table → Structure → Sprite → TableGridMixin`，`Structure` 的
   `update` / `move_by` / `y` / `get_dock_geometry` / `_iter_sprites_with_depth`
   （多为 `raise NotImplementedError`）会**逐个压掉** mixin 的实现，实测表现为：
   取不到 dock 几何 → `.ports` 抛错、`move_by` 抛错、`get_sprite_order` 抛错
   （渲染器画不出东西）、`depth_base_y()` 退化成 0.0（全局排序错乱）。

2. **import 路径必须带 `src.` 前缀。**
   `from src.core.build_game.grid.table_grid import TableGridMixin`。
   裸写 `from core...` 会让同一个文件被加载成**两个模块对象 / 两个类**
   （sys.path 同时含工程根与 src 时），是 LAYER_SYSTEM_V2_DESIGN §P2 记录的那类 bug。

## 为什么不用 TiledRect 当 shape

`TableGridMixin` 自带两层容器，**不需要** `shape.get_space()`；它只要
`shape.center / centerx / centery`。而 `TiledRect` 明确禁止 1×1 与 N×1
（那是墙的母题规则），桌子却天然可以是一张 1×1 的小桌。因此这里用
`FloatRect`（像素尺寸）当 shape，尺寸校验由本类自己按“桌子”的语义来做。
"""

import pygame

from src.core.build_game.grid.table_grid import TableGridMixin
from src.core.build_game.structure import Structure, RenderOrder
from src.core.build_game.tiledshape import FloatRect
from src.core.build_game.port import Port, RULES, register_rule
from src.utils.layer_v2 import OcclusionPolicy


class TableCornerPort(Port):
    """桌子四角端口：四个方向皆可连，每口只能连一个（max=1，连上即“满”→ DPM 红）。"""

    port_type = "TableCorner"
    connectable_sides = ("top", "bottom", "left", "right")
    max_connections = 1


def register_table_rules(log=None) -> list:
    """注册桌子族连接规则（桌↔桌拼桌）—— **幂等**，缺什么补什么、不重复注册。

    与 `register_wall_family_corner_rules` / `register_floor_corner_rules` 同款约定：
    规则表是全局单点，重复注册同一类型对会抛错，所以这里先查再补。
    """
    def make_cb():
        def cb(a, b):
            if log is not None:
                log.append(f"{a.short()} ↔ {b.short()}")
        return cb

    if RULES.get("TableCorner", "TableCorner") is not None:
        return [RULES.get("TableCorner", "TableCorner")]
    return [register_rule("TableCorner", "TableCorner",
                          action="docking", on_connect=make_cb())]


class Table(TableGridMixin, Structure):
    """桌子：双层格网格结构（table 层 = 桌面本体，object 层 = 桌上的东西）。

    - 形状：矩形网格 (grid_w × grid_h) 格，每格 (cell_w × cell_h) 像素；
      1×1 表示“一张单桌”，N×M 表示由多格拼成的大桌。
    - 端口：四角各一个 `TableCornerPort`（1×1 时就是那唯一一格，端口名 `center`）。
    - 深度：作为**一件家具**按自己的**前沿（南缘）**参与全局排序（`depth_base_y`），
      所以玩家躲到桌子北侧时桌子会正确地盖住他；结构**内部**则让板面沉底、
      桌上物体按各自那一行排 —— 详见 `TableGridMixin` 的 ①② 两条基准线。
    - 遮挡：参与通用遮挡判定（玩家被桌面挡住时桌子淡出），见下。
    """

    structure_type = "table"

    # 遮挡淡出：**参与**通用遮挡判定（和墙/房子同款），不要 opt-out。
    # 判定仍是通用规则（layer_v2.occluders_of）：绘制顺序排在焦点之后 **且**
    # 与焦点的实际绘制范围像素相交 —— 两条都满足才淡。所以：
    #   - 玩家站在桌子北侧、被桌面盖住 → 桌子淡出，玩家可见；
    #   - 玩家站在桌子前面 → 玩家本来就画在桌子之上，不会触发。
    #
    # ⚠️ 这条依赖 TableGridMixin.depth_base_y() 取的是**桌子前沿（南缘）**：
    #    若它退回成“北缘 − 1”（曾经的写法），桌子在全局永远排在最远，
    #    occluders_of 的“更近”条件永远不成立 —— 就会表现为“桌子不检测遮挡”。
    occlusion_policy = OcclusionPolicy.NONE

    def __init__(self,
                 grid_size: tuple[int, int] = (1, 1),
                 cell_size: tuple[int | float, int | float] = (64, 64),
                 center: tuple[float, float] = (0, 0),
                 layer_offset: int = RenderOrder.MIDDLE.value):
        Structure.__init__(self, layer_offset)

        gw, gh = int(grid_size[0]), int(grid_size[1])
        if gw < 1 or gh < 1:
            raise ValueError(f"桌子网格尺寸需 >=1：收到 {grid_size!r}")
        self._grid_width, self._grid_height = gw, gh
        self._cell_w, self._cell_h = cell_size

        # 只要“中心 + 尺寸” —— 两层容器在 mixin 里，不用 TiledRect.get_space()
        self.shape = FloatRect(0.0, 0.0, gw * self._cell_w, gh * self._cell_h)
        self.shape.center = center

        # 显式建好两层空间（mixin 契约）
        self._init_table_grid()

        self.sprite_types = {"top": pygame.sprite.Sprite}

    # ================= 端口 =================
    def port_node_for_cell(self, cell: tuple[int, int]):
        """四角各一个口；1×1 时四角重合，仍是同一个格 → 一个口。"""
        gw, gh = self._grid_width, self._grid_height
        corners = {(0, 0), (gw - 1, 0), (0, gh - 1), (gw - 1, gh - 1)}
        return TableCornerPort if cell in corners else None

    def port_name_for(self, side: str, index: int, cell: tuple[int, int]):
        """四角语义名；1×1 单桌没有“角”可言，命名为 `center`。"""
        gw, gh = self._grid_width, self._grid_height
        if gw == 1 and gh == 1:
            return "center" if cell == (0, 0) else None
        name_map = {
            (0, 0): "top_left",
            (gw - 1, 0): "top_right",
            (0, gh - 1): "bottom_left",
            (gw - 1, gh - 1): "bottom_right",
        }
        return name_map.get(cell)

    def on_topology_settled(self):
        """桌面是整块统一板面，拼接后无需换图 —— 保留为显式的自决策入口。"""
        return None

    def _map(self):
        """行映射：table 层整层都是同一种板面（与 Floor._map 同款声明式写法）。"""
        return [{"rows": (0, self._grid_height), "type": "top"}]

    def sprite_factory(self, typ):
        return self.sprite_types[typ]()

    def debug(self) -> str:
        """观测：网格/中心/两层占用/端口。排查“桌子看不见/口连不上”用。"""
        lines = [
            f"{type(self).__name__} grid={self._grid_width}x{self._grid_height} "
            f"cell=({self._cell_w},{self._cell_h}) center={self.shape.center}",
            f"  depth_base_y={self.depth_base_y():.1f} "
            f"top_left={tuple(round(v, 1) for v in self._get_top_left())}",
        ]
        for layer in self._LAYER_BOTTOM_UP:
            cells = sorted(self._layer_space(layer))
            lines.append(f"  {layer:6s} 层: {len(cells)} 格 {cells}")
        try:
            lines.append(f"  ports: {sorted(self.ports)}")
        except Exception as e:                     # 未实现 dock 几何时不该静默
            lines.append(f"  ports: 不可用 ({type(e).__name__}: {e})")
        return "\n".join(lines)


class WoodTable(Table):
    """木桌（具体）：`table` 层满铺木桌板面，贴图 = House Decoration 贴图集第 30 号。

    第 30 号的来历：`assets/terrain/Tilesets/House Decoration.tsx` 自己声明
    `tilewidth=64 tileheight=64 columns=9 tilecount=54`，即贴图集是 9 列 × 6 行
    的 64×64 瓦片；本地图集按 0 起编号，**30 号 = 第 3 行第 3 列 = 那张矩形木桌**。
    （`setup.py` 原先按 8×6 切，会切歪并让所有编号错位，已一并修正为 9×6。）
    """

    TABLE_TILE_ID = "terrain/graphics/environment/House Decoration_30"

    def __init__(self, grid_size: tuple[int, int] = (1, 1),
                 center: tuple[float, float] = (0, 0),
                 cell_size: tuple[int, int] = (64, 64)):
        Table.__init__(self, grid_size, cell_size, center)
        # 延迟导入实体类：避免无显示环境 import 本模块就拉起完整资源链（与横/竖墙一致）
        from src.game.entity import TableTopTile
        self.sprite_types["top"] = TableTopTile
        self.fill_layer(self.LAYER_TABLE, self.sprite_factory, "top")


# 兼容旧名（占位阶段曾用过 OnTheTable 这个名字）：它就是“桌子”本体。
OnTheTable = Table
