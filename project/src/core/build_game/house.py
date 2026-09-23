"""house.py —— 自遮挡的组合房子（House occluder）。

普通 Structure 是“原子对象”：渲染时把整组【叶子】当一整块、按结构整体
的 y() 参与全局深度排序，再一次性展开。因此无法把一个“在房子里面”的角色
插到【前墙】与【后墙】之间 —— 角色要么整座房子之前、要么整座房子之后。

House 解决了这一点：它是 occluder（is_occluder = True）。渲染器遇到它时，
把它的叶子【逐片】丢进全局深度排序，而不再是当成一整块。于是：

  角色/物体在地板坐范围内 → 其深度自然落在 后墙(顶墙) 与 前墙(底墙) 之间
                            → 被前墙遮挡、又能盖住后墙（正确“在房子里”的观感）。
  角色/物体在房子以南/以北 → 靠自身几何深度排在整座房子之前或之后（无需手写判断）。

实现方式：把 RectangleHouse 组装出的地板 + 四面墙，作为 Leaf 源合并成一个
Structure，并对每片叶子打上 under / over 标记：
    under = 地板 + 顶/后墙 + 左右侧墙   （在内部对象之前画）
    over  = 底/前墙                     （在内部对象之后画）

子结构本身不加入世界（不作为绘制对象），只提供它们的叶子；House 是唯一
被渲染/被世界管理的东西。
"""

import pygame

from src.core.build_game.structure import Structure, RenderOrder
from src.utils.layer_v2 import depth_y, OcclusionPolicy


def settings_side_face():
    """settings.WALL_SIDE_FACE 的 (贴图, 占比)；没开则 None。

    惰性且防御性读取：拿不到 settings 或读失败时返回 None —— 引擎默认行为
    不因为一个可选开关而变脆。
    """
    try:
        import settings as _s
        tile = getattr(_s, "WALL_SIDE_FACE", None)
        if tile is None:
            return None
        fill = float(getattr(_s, "WALL_SIDE_FACE_FILL", 1.0) or 1.0)
    except Exception:      # noqa: BLE001
        return None
    return (tile, fill)


def _apply_settings_side_face(house) -> None:
    """按 settings.WALL_SIDE_FACE 给房子补侧面厚度（默认 None = 什么都不做）。"""
    spec = settings_side_face()
    if spec is not None:
        house.set_side_face(spec[0], spec[1])


class House(Structure):
    """自遮挡房子：把地板 + 四面墙合并成一套叶子，并声明为 occluder。

    构造参数直接接收已组装好的子结构（这些子结构通常由 RectangleHouse 产出并在
    内部完成了端口连接吸附）。可用 :meth:`House.build` 走 RectangleHouse 一步到位。

    - is_occluder : 渲染器据此把本结构叶子逐片参与全局深度排序。
    - interior_rect() : 地板 footprint（用于判“在不在房子里”/编辑器）。
    - render_split()  : (under_leaves, over_leaves)。
    """

    structure_type = "house"
    is_occluder = True

    # House 内部的叶子深度用纯 centery（不加 layer_offset 常数），
    # 以便和角色/物体的 centery 在同一尺度上比较、自然交错。
    layer_offset = RenderOrder.BOTTOM.value

    def __init__(self, floor, top_wall, bottom_wall, left_wall, right_wall,
                 *, layer_offset: int = RenderOrder.BOTTOM.value):
        super().__init__(layer_offset)
        self._parts = {
            "floor": floor,
            "top_wall": top_wall,
            "bottom_wall": bottom_wall,
            "left_wall": left_wall,
            "right_wall": right_wall,
        }
        self._under: list[pygame.sprite.Sprite] = []
        self._over: list[pygame.sprite.Sprite] = []
        self._all_leaves: list[pygame.sprite.Sprite] = []
        # 额外部件（边缘拆分出来的补墙小段等）：[(名字, "under"/"over")]
        self._extra_parts: list[tuple[str, str]] = []
        # 房子级的遮挡策略声明：会被转发到各部件（因为淡出单位是部件，不是房子）
        self._occlusion_policy = OcclusionPolicy.FADE
        for part in self._parts.values():
            if part is not None:
                part.occlusion_policy = self._occlusion_policy
        self._collect_leaves()

    # ---- 遮挡策略：房子级声明向下转发给各部件 ----
    # 淡出单位是【部件】（每面墙各自淡），策略查询问到的也是部件，
    # 所以房子级的声明必须落到部件上才有效，否则 wallkit.house(occlusion=...)
    # 会静默失效。
    @property
    def occlusion_policy(self):
        return self._occlusion_policy

    @occlusion_policy.setter
    def occlusion_policy(self, value):
        self._occlusion_policy = value
        for part in self._parts.values():
            if part is not None:
                part.occlusion_policy = value

    # ---- 一步构建（走 RectangleHouse 组装并把叶子收进来） ----
    @classmethod
    def build(cls, length: int, width: int, height: int,
              cell: tuple = (64, 64),
              center: tuple[float, float] = (0, 0),
              use_wood: bool = True,
              layer_offset: int = RenderOrder.BOTTOM.value) -> "House":
        """构建一个矩形房子（地板 length×width + 四面墙），返回 House。

        连接日志保留在返回值上（house.build_log）。
        """
        from src.core.build_game.rectangle_house import RectangleHouse
        ra = RectangleHouse(length, width, height, cell, center, use_wood)
        house = cls(ra.floor, ra.top_wall, ra.bottom_wall,
                    ra.left_wall, ra.right_wall, layer_offset=layer_offset)
        house.build_log = list(ra.log)
        _apply_settings_side_face(house)
        return house

    # ---- 叶子收集 / 分类 ----
    @staticmethod
    def _part_leaves(part) -> list:
        """取子结构按内部深度排好的叶子；子结构本身不绘制，只取叶子。"""
        if part is None:
            return []
        if hasattr(part, "iter_sorted_leaf_sprites"):
            return list(part.iter_sorted_leaf_sprites())
        if hasattr(part, "get_sprite_order"):
            return list(part.get_sprite_order())
        if getattr(part, "image", None) is not None:
            return [part]
        return []

    def _collect_leaves(self) -> None:
        under_parts = ("floor", "top_wall", "left_wall", "right_wall")
        over_parts = ("bottom_wall",)
        # 额外部件（见 add_part）：按声明的层并入两侧
        extra = list(getattr(self, "_extra_parts", []) or [])
        extra_under = [n for n, layer in extra if layer == "under"]
        extra_over = [n for n, layer in extra if layer == "over"]
        self._under = []
        self._over = []
        # 叶子 → 所属部件（分片排深度用：同一面墙的格子共用该墙的地面基准线）
        self._leaf_part = {}
        for name in tuple(under_parts) + tuple(extra_under) \
                + tuple(over_parts) + tuple(extra_over):
            part = self._parts.get(name)
            leaves = self._part_leaves(part)
            for leaf in leaves:
                self._leaf_part[id(leaf)] = part
            if name in under_parts or name in extra_under:
                self._under.extend(leaves)
            else:
                self._over.extend(leaves)
        self._all_leaves = self._under + self._over
        self._order_cache = None

    def _invalidate_order(self) -> None:
        super()._invalidate_order()
        # 叶子是外部子结构的引用；位置变化会导致深度变化，故一并清空。
        self._all_leaves = self._under + self._over

    # ---- 遮挡查询（渲染 / 编辑器 / 测试用） ----
    def render_split(self):
        """返回 (under_leaves, over_leaves)：内部对象之前 / 之后画的叶子。"""
        return list(self._under), list(self._over)

    @property
    def interior_rect(self) -> pygame.Rect:
        """地板 footprint（世界坐标矩形）—— “在不在房子里”的判定区域。"""
        floor = self._parts.get("floor")
        wp = getattr(floor, "world_position", None) if floor is not None else None
        if wp is not None:
            return pygame.Rect(int(wp.x), int(wp.y), int(wp.width), int(wp.height))
        return self.world_position

    # ---- Structure 契约：深度基准 / 叶子深度 ----
    def depth_base_y(self) -> float:
        """房子整体基准 = 各部件基准线的最大值（最靠前的那条地面线）。

        不走 world_position（那会经 get_sprite_order 回环遍历叶子），
        只向子部件要它们的纯几何基准线。
        """
        vals = []
        for part in self._parts.values():
            b = getattr(part, "depth_base_y", None)
            if callable(b):
                try:
                    vals.append(float(b()))
                except Exception:
                    continue
        return max(vals) if vals else float(self.centery)

    def leaf_base_y(self, leaf) -> float:
        """本片叶子所站的【地面线】：向它所属部件（那面墙/地板）逐格问。

        这是 bottom 锚点下房子仍正确的关键：
          - 前墙/后墙（立面）→ 整面共用墙脚线，故整面前墙排在屋内角色之后、
            整面后墙排在之前；
          - 地板（水平地面）→ 整块画在一切"站在它上面"的东西之前；
          - 侧墙（沿地面铺开的段）→ 逐行用自己那一行的地面线，与屋内/墙边
            的实体自然交错。
        若改成"每片叶子各自的下缘"，5 行高的前墙上半截会排到屋内角色身后，
        出现"半堵墙穿到人身前/身后"的破图。
        """
        part = self._leaf_part.get(id(leaf))
        getter = getattr(part, "leaf_base_y", None) if part is not None else None
        if callable(getter):
            try:
                return float(getter(leaf))
            except Exception:
                pass
        return depth_y(leaf)

    def leaf_depth(self, leaf) -> float:
        """全局排序深度 = 本片叶子的地面线 + 房子自己的 layer_offset。

        注意用房子自己的 layer_offset（而不是部件的），保证所有部件在同一尺度上。
        """
        return self.leaf_base_y(leaf) + float(self.layer_offset or 0)

    # ---- 遮挡交互策略：按通用规则走（谁挡住玩家谁淡出） ----
    #
    # 这里【不再】给前墙开特例。理由是几何事实：前墙是 3 行高的立面，墙脚在地板
    # 南缘，整面画在最后，会往北盖住地板的南 3 格 —— 玩家走进这 3 格就被吞掉。
    # 但这并不是房子独有的毛病，任何"排得更近且像素相交"的墙都一样，
    # 所以统一交给默认的 FADE 处理，而不是在 House 里写一条特判。
    #
    # 想让某个部件永不淡出，就在 occlusion_policy_for 里对它返回 NONE。
    def occlusion_owner_of(self, leaf):
        """淡出单位 = 本片叶子所属的【部件】—— 每面墙各自独立淡出。

        房子是"多面墙拼出来的自遮挡结构"。把整栋当一个淡出单位是错的观感：
        玩家被前墙挡住时，后墙/左右侧墙/地板会跟着一起变淡，整栋房子像褪色。
        按部件分之后，只有真正挡住玩家的那面墙退开，其他墙纹丝不动。
        """
        part = self._leaf_part.get(id(leaf))
        return part if part is not None else self

    @property
    def front_wall_leaves(self) -> list:
        """前墙（over）的叶子 —— 诊断/观测用。"""
        return list(self._over)

    # ---- 侧面厚度：转发给各部件（只有竖墙会真正生效） ----
    def set_side_face(self, tile, fill: float = 1.0):
        """给本房子所有"有侧面可补"的部件声明侧面填充，并立即生效。

        实际生效的是左右两道**竖墙**（实测它们的贴图只占半格 32px，另半格是空的）。
        前/后横墙用的 40/41/42 本就是满格贴图、地板也是整块，都会自动跳过。
        tile=None 表示关掉，恢复纯几何默认。

        不需要清排序缓存：深度键走世界坐标，与贴图内容无关（见 wall_grid 的
        _iter_sprites_with_depth —— 主键是格子地面线，次键是格子下缘）。
        """
        # 记下来：build 之后才 add_part 挂上来的部件（房子边缘拆出来的补墙小段等）
        # 也必须是同样的厚度，否则拼接处会出现"一段厚一段薄"
        self._side_face_spec = None if tile is None else (tile, float(fill))
        for part in self._parts.values():
            setter = getattr(part, "set_side_face", None)
            if callable(setter):
                setter(tile, fill)
        return self

    def side_face_spec(self):
        """本房子当前声明的侧面填充 (贴图, 占比)；没有则回落到 settings 的开关。"""
        spec = getattr(self, "_side_face_spec", None)
        return spec if spec is not None else settings_side_face()

    def side_face_parts(self) -> list:
        """哪些部件支持侧面填充（自省/调试用）。"""
        return [name for name, part in self._parts.items()
                if callable(getattr(part, "set_side_face", None))]

    def _iter_sprites_with_depth(self):
        for leaf in self._all_leaves:
            # 主键 = 该叶子所属部件的地面基准线 + layer_offset（与全局 depth_sorted_draw_units 同尺度）
            # 次键 = 叶子自身下缘 Y（同一面墙内：上排先画、下排后画）
            yield leaf, (self.leaf_depth(leaf), depth_y(leaf))

    def iter_sorted_leaf_sprites(self) -> list:
        return self.get_sprite_order()

    def y(self) -> float:
        return self.centery

    @property
    def centerx(self) -> float:
        r = self.world_position
        return float(r.centerx) if r is not None and r.width else 0.0

    @property
    def centery(self) -> float:
        r = self.world_position
        return float(r.centery) if r is not None and r.height else 0.0

    def move_by(self, dx: float, dy: float) -> None:
        for part in self._parts.values():
            if part is not None and hasattr(part, "move_by"):
                part.move_by(dx, dy)
        self._invalidate_order()

    def update(self) -> None:
        """重算各部件叶子位置（契约要求：Structure.update 没有默认效果）。

        房子自己不做布局，转发给各部件 —— 它们各自按 shape 重新摆格子。
        漏掉它会出大问题：世界 / pygame Group 调 update 时会直接抛
        NotImplementedError（契约检测表就是把这个缺口揪出来的）。
        """
        for part in self._parts.values():
            upd = getattr(part, "update", None)
            if callable(upd):
                upd()
        self._invalidate_order()

    # ------------------------------------------------------------------ #
    # 端口代理
    # ------------------------------------------------------------------ #
    def part(self, name):
        """取某个子部件（floor / top_wall / bottom_wall / left_wall /
        right_wall，以及 add_part 挂上来的额外部件）。"""
        return self._parts.get(name)

    def add_part(self, name: str, part, *, layer: str = "under"):
        """把【额外部件】挂进房子（与构造时那五个标准部件同权）。

        用途：一条边被拆开之后补回来的墙段（见 house_edge.HouseEdge.fill_gaps）——
        它们本来就属于这座房子，应该跟着房子一起移动/更新/淡出，而不是散落在世界里
        当独立物体。挂上来之后：

          · 叶子并入 under/over 两侧，参与逐片全局深度排序（与自遮挡同一套机制）；
          · move_by / update / occlusion_policy / set_side_face 自动覆盖它；
          · part(name) 能取到，port_names() 里也会出现它的端口。

        :param layer: "under"（在屋内对象之前画，后墙/侧墙/地板用）/
                      "over"（在屋内对象之后画，南面的前墙用）
        """
        if part is None:
            raise ValueError("part 不能为 None")
        if name in self._parts and self._parts[name] is not None:
            raise KeyError(f"房子已经有部件 {name!r} 了；换一个名字或先摘掉它")
        layer = str(layer or "under").lower()
        if layer not in ("under", "over"):
            raise ValueError(f"layer 必须是 'under' / 'over'，收到 {layer!r}")
        self._parts[name] = part
        self._extra_parts.append((name, layer))
        policy = getattr(self, "_occlusion_policy", None)
        if policy is not None:
            try:
                part.occlusion_policy = policy
            except Exception:      # noqa: BLE001
                pass
        # 侧面厚度：晚挂上来的墙段必须和房子里其他墙一样厚，否则拼接处一段厚一段薄
        spec = self.side_face_spec()
        setter = getattr(part, "set_side_face", None)
        if spec is not None and callable(setter):
            try:
                setter(spec[0], spec[1])
            except Exception:      # noqa: BLE001
                pass
        self._collect_leaves()
        return part

    def extra_parts(self) -> list:
        """(名字, 层) 列表：add_part 挂上来的额外部件（自省/调试用）。"""
        return list(getattr(self, "_extra_parts", []) or [])

    def port_names(self) -> list:
        """自省：本结构能通过 port() 拿到的端口名。

        两种形态：
          · "<部件>:<端口>"  —— 子部件的具名端口（横墙四角、竖墙头/尾/底）；
          · "<端别>"         —— 房子拆边后装的【边缘单格口】别名（north/south/…）。
        """
        out = list(self.ports)
        return out

    @property
    def ports(self) -> dict:
        """组合结构的统一端口视图：{名字: Port}。

        子部件的具名端口统一带上部件名前缀（"left_wall:top"），避免不同部件之间
        重名（左右竖墙都有 "top"）；拆边后的边缘单格口按**端别**暴露一个别名
        （"east" → 该边中点的那个口），方便"整条边接一条走廊"这种粗粒度调用。

        与 `Hall.ports`（hall.py）**同一套口径** —— 组合结构的端口都从这里看，
        外部不需要知道"房子是拼出来的、走廊是另装的"。
        """
        out = {}
        for name, part in self._parts.items():
            ports = getattr(part, "ports", None)
            if isinstance(ports, dict):
                for pname, port in ports.items():
                    out[f"{name}:{pname}"] = port
        # 拆边后的边缘单格口：按端别给一个别名（取该边中点那一格的口）
        for side, edge in (getattr(self, "_house_edges", {}) or {}).items():
            cells = edge.ports()
            if cells:
                mid = list(cells.values())[len(cells) // 2]
                out.setdefault(side, mid)
        return out

    def port(self, name: str):
        """按名取端口 —— 组合结构的统一入口（与 `Hall.port` 同口径）。

        支持的写法：
          · "<部件>:<端口>"   指名道姓，例如 "left_wall:top"（最稳）；
          · "<部件内的唯一名>" 在所有子部件里找唯一匹配，例如 "top"；
          · "<端别>"          拆边后的边缘口别名，例如 "east"。

        找不到 / 不唯一都抛 KeyError（并把候选列出来），不静默返回 None ——
        静默 None 会让"连不上"表现成"什么都没发生"，很难查。
        """
        if not name:
            raise KeyError("端口名不能为空")

        if ":" in name:
            part_name, _, port_name = name.partition(":")
            part = self._parts.get(part_name)
            ports = getattr(part, "ports", None) if part is not None else None
            if not isinstance(ports, dict):
                raise KeyError(f"House 没有部件 {part_name!r}（或它没有端口）")
            if port_name not in ports:
                raise KeyError(
                    f"部件 {part_name!r} 没有端口 {port_name!r}；"
                    f"可选：{sorted(ports)}")
            return ports[port_name]

        # 拆边后的边缘口别名（"east" 之类）优先——它们是"整条边"的入口
        for side, edge in (getattr(self, "_house_edges", {}) or {}).items():
            if side == name:
                cells = edge.ports()
                if cells:
                    return list(cells.values())[len(cells) // 2]

        hits = []
        for part_name, part in self._parts.items():
            ports = getattr(part, "ports", None)
            if isinstance(ports, dict) and name in ports:
                hits.append((part_name, ports[name]))
        if len(hits) == 1:
            return hits[0][1]
        if not hits:
            raise KeyError(f"House 里没有端口 {name!r}；可用：{self.port_names()}")
        raise KeyError(
            f"House 里端口 {name!r} 不唯一，来自 {[h[0] for h in hits]}；"
            f"请写成 '<部件>:{name}'")

    # ------------------------------------------------------------------ #
    # 边缘接口（HouseEdge）的端口可见性
    # ------------------------------------------------------------------ #
    def edge_ports_all(self) -> list:
        """本房子装过的【边缘单格口】（HouseEdge 的端口，可能跨多条边）。"""
        out = []
        for edge in (getattr(self, "_house_edges", {}) or {}).values():
            out.extend(edge.ports().values())
        return out

    def get_all_ports(self) -> list:
        """子部件端口 + 边缘单格口。

        为什么要把边缘口补进来：结构存取（structure_persist）靠
        get_all_ports()/ports 收集"跨结构连接"，边缘口若不出现在这里，
        "走廊接在这条边上"这条连接就会读档丢失（走廊还在、接缝没了）。
        """
        ports = list(super().get_all_ports())
        ports.extend(self.edge_ports_all())
        return ports

    def port_at_side(self, side: str, cell):
        """按 (边, 格) 取端口：先查边缘单格口，再回落到常规部件（存档回读用）。"""
        cell = tuple(cell) if cell is not None else None
        for port in self.edge_ports_all():
            if port.cell == cell and side in port.connectable_sides:
                return port
        return super().port_at_side(side, cell)

    def get_port_markers(self) -> list:
        """聚合本房子所有子结构(地板/四面墙)的端口标记。

        House 本身不直接持有端点 —— 它是把已装配好的子结构叶子合并成的组合
        “宿主结构”。因此端口查询只能从子结构聚合出它们的端口标记，否则 DPM
        叠加层会遇到 House 就只字不画（地板/墙的口全部丢失）。

        返回 [(world_rect, state), ...]，按【世界矩形】去重（跨部件的同世界格
        保留最高状态：空闲绿 < 已连未满黄 < 已满占用红）。
        """
        by_rect = {}
        for part in self._parts.values():
            if part is None:
                continue
            gm = getattr(part, "get_port_markers", None)
            if not callable(gm):
                continue
            try:
                for wp, state in gm():
                    key = (int(wp.left), int(wp.top), int(wp.width), int(wp.height))
                    cur = by_rect.get(key)
                    if cur is None:
                        by_rect[key] = [wp, state]
                    elif state > cur[1]:
                        cur[1] = state
            except Exception:
                continue
        # 边缘单格口（HouseEdge）：它们挂在房子本身而不是部件上，单独补进来，
        # 否则编辑器里的端口标记会漏掉"这条边还能接走廊的格"。
        for port in self.edge_ports_all():
            try:
                cx, cy = port.cell_center_world()
            except Exception:
                continue
            cw = float(getattr(self._parts.get("floor"), "_cell_w", 64) or 64)
            ch = float(getattr(self._parts.get("floor"), "_cell_h", 64) or 64)
            key = (int(cx - cw / 2), int(cy - ch / 2), int(cw), int(ch))
            state = port.marker_state
            cur = by_rect.get(key)
            if cur is None:
                by_rect[key] = [pygame.Rect(key[0], key[1], key[2], key[3]), state]
            elif state > cur[1]:
                cur[1] = state
        return [list(v) for v in by_rect.values()]

    def __repr__(self):
        return (f"{type(self).__name__}(type={self.structure_type!r}, "
                f"leaves={len(self._all_leaves)}, "
                f"id={self.instance_id!r})")
