"""table_grid.py —— 桌面（table）的「双层格子网格」机制 mixin。

## 为什么不能套用 WallGridMixin

`WallGridMixin` 的存储是 `self.shape.get_space()`（`tiledshape.RectSpace`），
而 `RectSpace._container` 是 **`{grid_pos: sprite}` —— 一格只能放一个精灵**，
并且 `register()` 在该格已被占用且 `overwrite=False` 时**直接抛 ValueError**。
桌子天生需要「同格两层」（桌面板面 + 摆在它上面的物体），所以：

    WallGridMixin 的容器装不下两层 —— 不是参数没给对，是数据结构不允许。

强行套用只有两条路，都不可接受：
  a) 给 `RectSpace` 加第二张容器表 → 动 `tiledshape` 的公共契约（墙/地板全受影响）；
  b) 把两层塞进同一个 sprite 里 → 破坏「一片叶子一个 world_position」的深度/遮挡前提
     （见 STRUCTURE_REQUIREMENTS §2.2 规则 4）。

因此本模块**另起炉灶**：自建 `{layer: {grid_pos: sprite}}` 两层空间。
但「格坐标 ↔ 像素」「dock 几何」「中心平移」这些与容器无关的几何，本文件
刻意与 `wall_grid.py` 保持**同款公式与同款命名**，以便调用方与后续抽取公共
基类时都不用改语义。

## 与 STRUCTURE_REQUIREMENTS.md 的对应关系

- §2.2 规则 2（水平地面整块垫底）：桌面本体（table 层）整块共用
  `depth_base_y() = 网格北缘 − 1`，保证整张桌子排在一切“站在它上面”的东西之前。
  桌上物体（object 层）改用**自己那一行**的下缘，让物体之间按前后行正确排序。
- §1.1（`_sprite_at_port` 必须返回该格精灵）：默认取 **table 层**（宿主自己那一格），
  这样 `on_port_changed → sprite.on_dock(...)` 换的是**桌子自己的**贴图，
  而不是桌上摆着的那个物体。要问“这格有没有被物体占住”，用 `object_at_port()`。
- §3.2 规则 1：本 mixin 不替子类决定淡出策略。桌子的策略由具体类显式声明
  （见 `on_the_table.Table.occlusion_policy`）。

mixin 依赖子类在 __init__ 里设置：
  self._grid_width, self._grid_height   : 网格列/行
  self._cell_w, self._cell_h            : 每格像素宽/高
  self.shape                            : 至少暴露 .center/.centerx/.centery
  self.layer_offset                     : Structure 已设
并在 `Structure.__init__` 之后调用一次 `self._init_table_grid()`。

⚠️ 继承顺序必须是 **mixin 在前**：`class Table(TableGridMixin, Structure)`。
   写成 `(Structure, TableGridMixin)` 会让 `Structure` 的 `update/`move_by`/
   `get_dock_geometry/_iter_sprites_with_depth`（多为 raise NotImplementedError）
   全部压掉本 mixin，桌子会“能构造但什么都不能做”。参见 on_the_table.py。
"""

import pygame

from src.core.build_game.tiledshape import RectDock
from src.utils.layer_v2 import depth_y


class TableGridMixin:
    """桌面通用的「双层格」注册 / 定位 / 读取 / 深度 / 移动 / 连接几何。"""

    LAYER_TABLE = "table"      # 下层：桌子的逻辑空间（板面 / 桌腿 / 桌面装饰）
    LAYER_OBJECT = "object"    # 上层：摆在桌面上的物体

    # 由下到上的绘制顺序：object 永远压住 table。
    _LAYER_BOTTOM_UP = (LAYER_TABLE, LAYER_OBJECT)

    # object 层（摆在桌面上的东西）的「贴面锚点偏移」（像素；x 右为正，y 下为正）。
    #
    # 为什么需要它：`_calc_pixel_position` 给的是**格子几何中心**，那是桌子的
    # 逻辑中心，不是它“看得见的板面”所在。桌上物若也放在格心，就会和桌面的中心
    # 糊在同一处 —— 平面拼贴感，没有立体感、也看不出是“放在桌上”。
    # 把物体整体上移一点，它的底面就落到桌面板面（偏上、退后）的那一块上，
    # 视觉上才成立。
    #
    # -10 是看着最舒服的一档：盆底落在板面上、桌面前沿仍露在下方，
    # 物体顶部也还留有余量（不贴格子上边、不越格）。
    # 想微调就改这里，或给某张桌子单独设实例属性 `table.object_offset = (0, -14)`。
    #
    # ⚠️ 这是**纯视觉**偏移，只改叶子的 world_position；
    #    深度仍按格子地面线算（见 cell_depth_base_y），所以不会污染 2.5D 排序——
    #    否则“上移多少”会连带改变它和别的物体的前后关系。
    LAYER_OBJECT_OFFSET = (0.0, -10.0)

    # ================= 层与空间 =================
    @classmethod
    def _validate_layer(cls, layer: str) -> None:
        if layer not in cls._LAYER_BOTTOM_UP:
            raise ValueError(
                f"未知层 {layer!r}；桌面只有 {cls._LAYER_BOTTOM_UP} 两层")

    def _init_table_grid(self) -> None:
        """建议在子类 __init__ 里调用一次：显式建好两层空间。"""
        self._grid_spaces = {layer: {} for layer in self._LAYER_BOTTOM_UP}
        self._leaf_meta_cache = None

    def _spaces(self) -> dict:
        """两句空间的字典（缺省惰性建立，容错子类忘了调 _init_table_grid）。"""
        spaces = getattr(self, "_grid_spaces", None)
        if spaces is None:
            self._init_table_grid()
            spaces = self._grid_spaces
        return spaces

    def _layer_space(self, layer: str) -> dict:
        """取某一层的 {grid_pos: sprite} 空间（缺省惰性建立）。"""
        self._validate_layer(layer)
        return self._spaces().setdefault(layer, {})

    def iter_layer_items(self, layer: str = None):
        """遍历 (layer, grid_pos, sprite)。layer=None 时按由下到上的层序给全部。"""
        spaces = self._spaces()
        layers = self._LAYER_BOTTOM_UP if layer is None else (layer,)
        for name in layers:
            self._validate_layer(name)
            for cell, sprite in spaces.get(name, {}).items():
                yield name, cell, sprite

    def _cell_in_bounds(self, grid_pos) -> bool:
        gx, gy = grid_pos
        return 0 <= gx < self._grid_width and 0 <= gy < self._grid_height

    # ================= 格坐标 <-> 像素（与 wall_grid 同款公式） =================
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

    def layer_pixel_offset(self, layer: str) -> tuple[float, float]:
        """某一层的“贴面锚点偏移”像素量（默认 table 层为 0，object 层见类注释）。

        可用实例属性覆盖：`table.object_offset = (0, -16)`。
        """
        self._validate_layer(layer)
        if layer != self.LAYER_OBJECT:
            return (0.0, 0.0)
        override = getattr(self, "object_offset", None)
        off = type(self).LAYER_OBJECT_OFFSET if override is None else override
        return (float(off[0]), float(off[1]))

    def _cell_pixel_position(self, grid_pos, layer: str = LAYER_TABLE,
                            top_left=None) -> tuple[float, float]:
        """某层某格的落位像素坐标 = 格心 + 该层贴面偏移。"""
        x, y = self._calc_pixel_position(grid_pos, top_left)
        ox, oy = self.layer_pixel_offset(layer)
        return (x + ox, y + oy)

    def grid_at_world(self, world_pos) -> tuple[int, int] | None:
        """世界坐标 → 格坐标；落在桌面外返回 None。"""
        left, top = self._get_top_left()
        gx = int((world_pos[0] - left) // self._cell_w)
        gy = int((world_pos[1] - top) // self._cell_h)
        if 0 <= gx < self._grid_width and 0 <= gy < self._grid_height:
            return (gx, gy)
        return None

    @property
    def center(self):
        return self.shape.center

    # ================= 注册 / 重排 =================
    def register_sprite(self, grid_pos, sprite,
                        layer=LAYER_TABLE, overwrite: bool = False):
        """把 sprite 摆到某一层的某格，返回该 sprite；该格已被占则返回 None。

        与 `WallGridMixin.register_sprite` 的差异（有意为之，勿照搬）：
        - 默认层是 **table 层**（桌面本体），不是“随便哪层”；
        - 占位时**返回 None 而不是抛异常** —— object 层靠这一点实现
          「一格一物」的自然占位语义（见 `place_object`）。
        """
        self._validate_layer(layer)
        if not self._cell_in_bounds(grid_pos):
            raise ValueError(f"格坐标 {grid_pos} 超出桌面网格 "
                             f"{self._grid_width}x{self._grid_height}")
        space = self._layer_space(layer)
        if grid_pos in space and not overwrite:
            return None
        space[grid_pos] = sprite
        self._invalidate_order()
        self._apply_pixel_position(sprite, self._cell_pixel_position(grid_pos, layer))
        self._decorate_leaf(sprite)      # 首次摆位后的收口（与 wall_grid 一致）
        return sprite

    def remove_sprite(self, grid_pos, layer=LAYER_TABLE):
        """摘掉某层某格的内容，返回被摘下的 sprite（没有则 None）。"""
        sprite = self._layer_space(layer).pop(grid_pos, None)
        if sprite is not None:
            self._invalidate_order()
        return sprite

    def update(self):
        self._invalidate_order()
        left, top = self._get_top_left()
        for layer, (gx, gy), sprite in self.iter_layer_items():
            pixel_pos = self._cell_pixel_position((gx, gy), layer, (left, top))
            self._apply_pixel_position(sprite, pixel_pos)
            self._decorate_leaf(sprite)

    def parse_and_register(self, config: list[dict], sprite_factory,
                           default_type: str = None,
                           layer: str = LAYER_TABLE):
        """把 rows/cols/type 规则铺到指定层。

        规则里可带 `"layer"` 覆盖参数 layer，方便一份 config 同时描述两层。

        ⚠️ 与 `WallGridMixin.parse_and_register` 的**有意差异**：
        那边的 `default_type` 默认是 `'middle'`（＝没被规则覆盖的格子也铺满）；
        这里默认 `None`（＝**只铺规则覆盖到的格子**）。因为桌面通常“table 层满铺
        板面、object 层只在指定格放东西”，满铺应由显式的 `fill_layer` 表达，
        而不是靠一个隐式默认值。跨 mixin 复用同一个调用写法时务必注意。
        """
        self._validate_layer(layer)
        type_map: dict[tuple[str, tuple[int, int]], str] = {}
        for rule in config:
            rule_layer = rule.get("layer", layer)
            self._validate_layer(rule_layer)
            rows = rule["rows"]
            cols = rule.get("cols", (0, self._grid_width))
            typ = rule["type"]
            for gy in range(rows[0], rows[1]):
                for gx in range(cols[0], cols[1]):
                    if self._cell_in_bounds((gx, gy)):
                        type_map[(rule_layer, (gx, gy))] = typ

        # 规则显式指定的格子（含跨层规则）
        for (target_layer, cell), typ in type_map.items():
            sprite = sprite_factory(typ)
            if sprite is not None:
                self.register_sprite(cell, sprite, layer=target_layer)

        # 参数 layer 这一层里没被规则覆盖的格子，用 default_type 补齐
        if default_type is not None:
            for gy in range(self._grid_height):
                for gx in range(self._grid_width):
                    cell = (gx, gy)
                    if (layer, cell) in type_map:
                        continue
                    sprite = sprite_factory(default_type)
                    if sprite is not None:
                        self.register_sprite(cell, sprite, layer=layer)

    def fill_layer(self, layer, sprite_factory, typ):
        """把整层的每一格都铺上 typ（例如 table 层满铺桌面板）。

        返回成功摆上的格数；已被占的格会被跳过（不覆盖，除非先 remove_sprite）。
        """
        self._validate_layer(layer)
        n = 0
        for gy in range(self._grid_height):
            for gx in range(self._grid_width):
                sprite = sprite_factory(typ)
                if sprite is not None and self.register_sprite(
                        (gx, gy), sprite, layer=layer) is not None:
                    n += 1
        return n

    # ================= 查询 =================
    def get_sprite_at_grid(self, grid_pos, layer=LAYER_TABLE):
        return self._layer_space(layer).get(grid_pos)

    def get_sprites_at_grid(self, grid_pos) -> dict:
        """该格两层的内容（缺的层不出现在返回的 dict 里）。"""
        spaces = self._spaces()
        return {layer: spaces[layer][grid_pos]
                for layer in self._LAYER_BOTTOM_UP
                if grid_pos in spaces.get(layer, {})}

    def get_sprite_at_world(self, world_pos, layer=LAYER_TABLE):
        cell = self.grid_at_world(world_pos)
        if cell is None:
            return None
        return self.get_sprite_at_grid(cell, layer)

    def get_object_at(self, grid_pos):
        """该格上摆的物体（没摆则 None）。"""
        return self.get_sprite_at_grid(grid_pos, self.LAYER_OBJECT)

    def get_table_at(self, grid_pos):
        """该格桌面本体（没铺则 None）。"""
        return self.get_sprite_at_grid(grid_pos, self.LAYER_TABLE)

    def has_object_at(self, grid_pos) -> bool:
        return grid_pos in self._layer_space(self.LAYER_OBJECT)

    def occupied_cells(self, layer: str = None) -> set:
        """某层（或全部层）已被占用的格集合 —— 给编辑器/存档用。"""
        spaces = self._spaces()
        layers = self._LAYER_BOTTOM_UP if layer is None else (layer,)
        out = set()
        for name in layers:
            self._validate_layer(name)
            out |= set(spaces.get(name, {}))
        return out

    def place_object(self, grid_pos, sprite, overwrite: bool = False):
        """把一个物体摆到桌面上：object 层「一格一物」，占位则返回 None。"""
        return self.register_sprite(grid_pos, sprite,
                                    layer=self.LAYER_OBJECT,
                                    overwrite=overwrite)

    # ================= 深度（2.5D 正确性） =================
    #
    # 这里有两个**用途不同**的基准线，务必分开（之前混成一个，导致桌子永远检测不到遮挡）：
    #
    #   ① depth_base_y()      —— 整张桌子作为【一件家具】对外的落地线，
    #                            给全局排序 + 遮挡判定用（occluders_of 靠它判断“谁更近”）。
    #   ② surface_base_y()    —— 桌面本体在【结构内部】排序时的沉底值，只为了让
    #                            “板面排在摆在它上面的东西之前”，纯内部语义。
    #
    # 混用的后果：把 ① 取成“北缘 − 1”时，桌子在全局永远排在最前（最远），
    # 于是它永远不可能“比焦点更近” —— occluders_of 的①条不成立，
    # 桌子就永远不算遮挡者（哪怕玩家明明被桌面盖住）。这正是“桌子不检测遮挡”的根因。

    def depth_base_y(self) -> float:
        """整张桌子作为【一件家具】参与全局排序的落地线 Y = 它的**前沿（南缘）**。

        与 `WallGridMixin.depth_base_y()`（格子网格下缘）同款公式、同一个道理：
        “桌子画到哪里，玩家就认为它站在哪里”。桌子是**立在地上的家具**，
        不是地面本身 —— 所以**不能**照 Floor 的“水平面整块取最北边 − 1”来算：
        那样它会一直排在最远，既不正确地压在更北的墙/家具前面，
        也永远不会被判定为“挡住玩家”。

        取南缘之后的行为才是对的：
          - 玩家站在桌子北侧（脚在桌子前沿以北）→ 玩家先画，桌子后画 → 桌子盖住玩家
            → 被判为遮挡者 → 淡出，玩家重新可见；
          - 玩家站在桌子南侧（前面）→ 玩家后画，压在桌子之上 → 桌子不算遮挡，不淡出。

        纯几何计算，不走 world_position/get_sprite_order：本方法会被
        `_iter_sprites_with_depth` 间接调用，走包围盒会回环递归。
        """
        _, top = self._get_top_left()
        return float(top) + float(self._grid_height) * float(self._cell_h)

    def surface_base_y(self) -> float:
        """桌面本体（table 层）在**结构内部**排序用的沉底值 = 网格北缘 − 1。

        只服务一件事：保证整张桌子的**板面**都排在摆在其上的**物体**之前
        （STRUCTURE_REQUIREMENTS §2.2 规则 2 的“地面先画”精神）。
        它是一个结构内的“沉底哨兵”，**不参与全局排序** —— 对外的落地线见
        :meth:`depth_base_y`。

        之所以必须严格小于任何物体那一行的地面线：否则南边那一行的板面会画在
        北边上那个物体之后，把高一点的物件下半截盖住。
        """
        _, top = self._get_top_left()
        return float(top) - 1.0

    def _row_bottom_y(self, gy: int) -> float:
        """某一「行」的地面线 Y（该行格子在世界里的下缘）。"""
        _, top = self._get_top_left()
        return float(top) + (int(gy) + 1) * float(self._cell_h)

    def cell_depth_base_y(self, grid_pos, layer: str = None) -> float:
        """某一格参与【结构内部】深度排序时用的「地面线」Y。

        - `LAYER_TABLE`（桌面本体）：整块共用 `surface_base_y()` → 桌面沉底；
        - `LAYER_OBJECT`（桌上物体）：用**自己那一行**的下缘 → 前后行正确分先后。
        - layer=None（未指明）：按“站在桌面上”处理，用逐行线（更保守）。
        """
        if layer == self.LAYER_TABLE:
            return self.surface_base_y()
        return self._row_bottom_y(grid_pos[1])

    def _leaf_meta(self, sprite):
        """叶子 → (layer, grid_pos, layer_index)；懒建缓存，几何变更时失效。"""
        cache = getattr(self, "_leaf_meta_cache", None)
        if cache is None:
            cache = {}
            for layer_index, layer in enumerate(self._LAYER_BOTTOM_UP):
                for cell, sp in (self._spaces().get(layer) or {}).items():
                    cache[id(sp)] = (layer, cell, layer_index)
            self._leaf_meta_cache = cache
        return cache.get(id(sprite))

    def leaf_base_y(self, leaf) -> float:
        """逐格逐层给地面线：找到叶子就用它那一格/那一层的语义，否则退回整桌基准线。"""
        meta = self._leaf_meta(leaf)
        if meta is None:
            return self.depth_base_y()
        layer, cell, _layer_index = meta
        return self.cell_depth_base_y(cell, layer)

    def _invalidate_order(self) -> None:
        self._leaf_meta_cache = None
        super()._invalidate_order()

    def _iter_sprites_with_depth(self):
        """主键 = 该格所站的地面线 Y + layer_offset（table 垫底 / object 逐行）；
        次键 = 层序（table 在下、object 在上，同格两层由此分先后）；
        末键 = 该格自身下缘 Y，保证同一行同一层内也稳定（画家序）。
        """
        off = float(self.layer_offset or 0)
        spaces = self._spaces()
        for layer_index, layer in enumerate(self._LAYER_BOTTOM_UP):
            for cell, sprite in spaces.get(layer, {}).items():
                yield sprite, (self.cell_depth_base_y(cell, layer) + off,
                               layer_index,
                               depth_y(sprite))

    def y(self):
        return self.shape.centery

    def set_center(self, new_center: tuple[float, float]):
        self.shape.center = new_center
        self.update()

    def move_by(self, dx: float, dy: float):
        self.set_center((self.shape.centerx + dx, self.shape.centery + dy))

    # ================= 连接几何 / 端口所在格 =================
    def get_dock_geometry(self) -> RectDock:
        return RectDock(self._grid_width, self._grid_height,
                        self._cell_w, self._cell_h,
                        self.shape.centerx, self.shape.centery)

    def _sprite_at_port(self, port, layer=LAYER_TABLE):
        """取端口格上的精灵 —— 默认看 **table 层**（宿主自己那一格）。

        为什么默认是 table 而不是 object：`Structure.on_port_changed` 会拿这个
        精灵去调 `on_dock(...)` 让它自决换贴图。桌子连接后该换图的是**桌子自己
        的面**，不是恰好摆在这一格上的那个物体。

        要问“这一格有没有被物体占住”（例如判断还能不能往上摆东西），用
        `object_at_port()`。
        """
        return self.get_sprite_at_grid(port.cell, layer)

    def object_at_port(self, port):
        """端口格上摆着的物体（没摆则 None）。"""
        return self.get_sprite_at_grid(port.cell, self.LAYER_OBJECT)
