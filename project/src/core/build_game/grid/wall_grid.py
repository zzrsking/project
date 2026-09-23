"""wall_grid.py —— 横/竖墙共通的“格子网格”机制 mixin（S4：拆继承）。

横墙与竖墙的 shape/尺寸/朝向本质不同，不应互相继承。但两者都要：
把 cell 精灵注册进一个 shape.get_space()、按中心+格尺寸摆放、解析行映射、按深度排序。
这些【真正共通】的网格片段收进本 mixin，由 HorizontalWall / VerticalWall 各自
直接继承 Structure 再混入本 mixin，各自实现 shape/网格/朝向 —— 不再通过继承复用。

mixin 依赖子类在 __init__ 里设置：
  self._grid_width, self._grid_height   : 网格列/行
  self._cell_w, self._cell_h            : 每格像素宽/高
  self.shape                            : 暴露 .center/.centerx/.centery/.get_space()(有 register/get/iter)
  self.layer_offset                     : Structure 已设
"""

import pygame

from src.core.build_game.tiledshape import RectDock
from src.utils.layer_v2 import depth_y


class WallGridMixin:
    """提供“矩形格墙”共通的注册/定位/读取/深度/移动/连接几何。"""

    # ---- 格坐标 <-> 像素 ----
    def _get_top_left(self) -> tuple[float, float]:
        cx, cy = self.shape.center
        return (cx - (self._grid_width * self._cell_w) / 2,
                cy - (self._grid_height * self._cell_h) / 2)

    def _calc_pixel_position(self, grid_pos, top_left=None):
        if top_left is None:
            top_left = self._get_top_left()
        left, top = top_left
        gx, gy = grid_pos
        return (left + gx * self._cell_w + self._cell_w / 2,
                top + gy * self._cell_h + self._cell_h / 2)

    @staticmethod
    def _apply_pixel_position(sprite, pixel_pos: tuple[float, float]):
        sprite.world_position.center = pixel_pos

    @property
    def center(self):
        return self.shape.center

    # ---- 注册 / 重排 ----
    def register_sprite(self, grid_pos: tuple[int, int], sprite, overwrite: bool = False):
        self._invalidate_order()
        self.shape.get_space().register(sprite, grid_pos, overwrite=overwrite)
        pixel_pos = self._calc_pixel_position(grid_pos)
        self._apply_pixel_position(sprite, pixel_pos)
        self._decorate_leaf(sprite)      # 首次摆位后的收口（墙体在此补侧脸等）

    def update(self):
        self._invalidate_order()
        left, top = self._get_top_left()
        for (gx, gy), sprite in self.shape.get_space():
            pixel_pos = self._calc_pixel_position((gx, gy), (left, top))
            self._apply_pixel_position(sprite, pixel_pos)
            self._decorate_leaf(sprite)

    def parse_and_register(self, config: list[dict], sprite_factory, default_type: str = 'middle'):
        type_map = {}
        for rule in config:
            rows = rule["rows"]
            cols = rule.get("cols", (0, self._grid_width))
            typ = rule["type"]
            for gy in range(rows[0], rows[1]):
                for gx in range(cols[0], cols[1]):
                    if 0 <= gx < self._grid_width and 0 <= gy < self._grid_height:
                        type_map[(gx, gy)] = typ
        for gy in range(self._grid_height):
            for gx in range(self._grid_width):
                typ = type_map.get((gx, gy), default_type)
                sprite = sprite_factory(typ)
                if sprite is not None:
                    self.register_sprite((gx, gy), sprite)

    # ---- 查询 / 深度 / 移动 ----
    def get_sprite_at_grid(self, grid_pos) -> pygame.sprite.Sprite | None:
        return self.shape.get_space().get(grid_pos)

    def get_sprite_at_world(self, world_pos) -> pygame.sprite.Sprite | None:
        left, top = self._get_top_left()
        dx = world_pos[0] - left
        dy = world_pos[1] - top
        grid_x = int(dx // self._cell_w)
        grid_y = int(dy // self._cell_h)
        if 0 <= grid_x < self._grid_width and 0 <= grid_y < self._grid_height:
            return self.get_sprite_at_grid((grid_x, grid_y))
        return None

    def depth_base_y(self) -> float:
        """整墙的地面基准线 Y（格子网格下缘）。

        纯几何计算，不走 world_position/get_sprite_order —— 因为本方法会被
        _iter_sprites_with_depth 间接调用，走包围盒会回环递归。

        用【还挂着的格】算下缘：被 mask_cells 挖过的墙（从一整面墙上切下来的一段）
        只对"它真正占着的那几行"负责，否则切出来的短段会按整面墙的墙脚线参与排序，
        跑到别的墙前面去。
        """
        top = self._get_top_left()[1]
        _, max_gy = self.visible_rows()
        return float(top) + float(max_gy + 1) * float(self._cell_h)

    # ---- 挖格 / 可见范围：把"整面墙"切成"一段墙" ----
    def visible_rows(self) -> tuple[int, int]:
        """当前还挂着的格覆盖的行区间 (min_gy, max_gy)（挖格后会变小，带缓存）。"""
        cache = getattr(self, "_visible_rows_cache", None)
        if cache is not None:
            return cache
        rows = [cell[1] for cell, _ in self.shape.get_space()]
        if not rows:
            cache = (0, int(self._grid_height) - 1)
        else:
            cache = (min(rows), max(rows))
        try:
            self._visible_rows_cache = cache
        except Exception:      # noqa: BLE001
            pass
        return cache

    def mask_cells(self, cells, *, drop_ports: bool = True) -> list:
        """把若干格从这堵墙上【挖掉】（不绘制、不参与深度、不留端口）。

        用途：横墙/竖墙都是"整块网格"结构，"只保留其中一段"无法靠构造参数表达 ——
        但可以先按原尺寸把整面墙造出来，再把不属于这一段的行列挖掉。这样**贴图逐格
        保留原墙的段语义**（头/中/尾/正立面都还在原来的行上），不会因为"这段只剩
        几格"就把头尾挤到一起、或者让墙在邻墙的上面/下面断开。

        :param cells: 要挖掉的格 [(gx,gy), ...]
        :param drop_ports: 顺手把这些格上的端口摘掉（挖掉的格不该还能被连）
        :return: 真正挖掉的格列表
        """
        space = self.shape.get_space()
        removed = []
        for cell in cells:
            cell = (int(cell[0]), int(cell[1]))
            if space.get(cell) is None:
                continue
            space.remove(cell)
            removed.append(cell)
            if drop_ports:
                try:
                    self._cell_ports_cache.pop(cell, None)
                except Exception:      # noqa: BLE001
                    pass
        if removed:
            self._invalidate_order()
        return removed

    def masked_cells(self) -> list:
        """已经挖掉的格（相对整块网格的补集），便于观测/调试。"""
        space = self.shape.get_space()
        out = []
        for gy in range(int(self._grid_height)):
            for gx in range(int(self._grid_width)):
                if space.get((gx, gy)) is None:
                    out.append((gx, gy))
        return out

    def _cell_is_masked(self, cell) -> bool:
        """这一格是不是被挖掉了？

        "挖掉" = 网格里本来有它、但空间里已经没有它的精灵。无纹理壳结构
        （例如没 parse_and_register 的 VerticalWall）一个精灵都没有，不算挖掉 ——
        否则它们的端口会全部消失。
        """
        space = self.shape.get_space()
        if space.get(cell) is not None:
            return False
        return bool(space.get_all())

    def _node_port_for_cell(self, cell):
        """挖掉的格不再生成端口：那一格已经不在这堵墙上了，不该还能被连。"""
        if self._cell_is_masked(cell):
            return None
        return super()._node_port_for_cell(cell)

    def _row_bottom_y(self, gy: int) -> float:
        """某一「行」的地面线 Y（该行格子在世界里的下缘）。"""
        _, top = self._get_top_left()
        return float(top) + (int(gy) + 1) * float(self._cell_h)

    def cell_depth_base_y(self, grid_pos) -> float:
        """某一格参与深度排序时用的「地面线」Y。

        默认 = 整墙基准线（**立面**语义）：横墙的每一行都只是同一面墙上的
        不同高度，整面墙站在同一条地面上，故整面共用一个基准线。
        水平铺在地面上的结构（地板、竖墙沿地面铺开的行）应覆写本方法，
        让每格/每行用自己真实站在的那条地面线。
        """
        return self.depth_base_y()

    def _cell_of_leaf(self, sprite):
        """叶子精灵 → 所在格坐标（懒建缓存，注册/移动时经 _invalidate_order 失效）。"""
        cache = getattr(self, "_leaf_cell_cache", None)
        if cache is None:
            cache = {id(s): cell for cell, s in self.shape.get_space()}
            self._leaf_cell_cache = cache
        return cache.get(id(sprite))

    def leaf_base_y(self, leaf) -> float:
        """逐格给地面线：找到叶子所在格就用该格的语义，找不到退回整墙基准线。"""
        cell = self._cell_of_leaf(leaf)
        if cell is None:
            return self.depth_base_y()
        return self.cell_depth_base_y(cell)

    def _invalidate_order(self) -> None:
        super()._invalidate_order()
        self._leaf_cell_cache = None
        self._visible_rows_cache = None

    def _iter_sprites_with_depth(self):
        space = self.shape.get_space()
        # 主键 = 该格所站的地面线 Y + layer_offset（立面=整墙共用；地面=逐格/逐行）；
        # 次键 = 该格自身下缘 Y：同一条基准线上「上排先画、下排后画」（画家序）。
        off = float(self.layer_offset or 0)
        for (gx, gy), sprite in space:
            yield sprite, (self.cell_depth_base_y((gx, gy)) + off, depth_y(sprite))

    def y(self):
        return self.shape.centery

    def set_center(self, new_center: tuple[float, float]):
        self.shape.center = new_center
        self.update()

    def move_by(self, dx: float, dy: float):
        self.set_center((self.shape.centerx + dx, self.shape.centery + dy))

    # ---- 连接几何 / 端口所在格 ----
    def get_dock_geometry(self) -> RectDock:
        return RectDock(self._grid_width, self._grid_height,
                        self._cell_w, self._cell_h,
                        self.shape.centerx, self.shape.centery)

    def _sprite_at_port(self, port):
        return self.get_sprite_at_grid(port.cell)


