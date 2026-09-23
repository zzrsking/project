import pygame
from enum import Enum

from src.core.build_game.port import Port, new_instance_id
from src.utils.layer_v2 import OcclusionPolicy
from utils.helpers import print_info


class RenderOrder(Enum):
    BOTTOM = 0
    MIDDLE = 1
    TOP = 2


class Structure(pygame.sprite.Sprite):
    """所有建筑结构的基类，负责管理精灵排序和层级偏移。

    Structure = 逻辑空间（形状）：
    - structure_type : 类型 ID（人类可读字符串，如 "wall"），观测/归属用
    - instance_id    : 实例 ID（谁连了谁），构造时自动生成
    - dock 几何       : 由子类实现 get_dock_geometry()
    - 边界端口        : 子类声明 port_class_for(side, index, cell) → Port 子类或 None，
                        Structure 懒生成/缓存每条边的 Port 列表（无端口处为 None）

    设计原则（2026 定稿）：
    - Structure 只保存逻辑与状态，**不参与绘制**；
    - 端口色标（绿=空闲 / 黄=已连接）是“调试特效”：由渲染器叠加绘制
      （受 settings.DISPLAY_PORT_MARKERS 开关控制），**不修改任何精灵的 image**。

    端口变化 → 贴图的**唯一路径**（别再另开入口）：
      Port.connect_to / Port.disconnect
        → 本基类的 on_port_changed（逐对：连上/断开）
        → 子类重写的 on_topology_settled（批次结束：读端口簇自决策）
        → 格子精灵自己换图
    """

    structure_type = "structure"   # 子类覆盖，例如 "wall"

    # 碰撞默认关闭：Structure 的逐格碰撞属未来“structure 碰撞编辑器”范畴，
    # 在实现前禁止被 add_game_object 误加进物理引擎（防 (0,0) 幽灵碰撞体）。
    is_collidable = False

    # 遮挡交互：默认不是自遮挡结构（整块作为绘制原子）。
    # 以前这个属性不存在、靠 layer_v2.is_occluder 的 getattr(..., False) 兜底 ——
    # 现在显式写出来，让任何读代码/工具都能看到这个默认值。
    is_occluder = False

    def __init__(self, layer_offset: int = RenderOrder.MIDDLE.value):
        super().__init__()
        self.in_active_block = True
        self.priority = 1
        self.layer_offset = layer_offset
        self.sprite_types = {}
        self.instance_id = new_instance_id()
        self._cell_ports_cache = {}   # cell -> Port | None（每格一个多向节点）
        self._order_cache = None      # 叶子深度排序缓存；几何变更时置空
        self.connections = []         # 已连接的对端 Structure 实例

    def _iter_leaf_sprites(self):
        """遍历本结构管理的叶子精灵；未实现时为空。"""
        try:
            for leaf in self.get_sprite_order():
                yield leaf
        except Exception:
            return

    def y(self):
        raise NotImplementedError

    # ---- 深度基准（Y 轴排序锚点 = 底部） ----
    def depth_base_y(self) -> float:
        """本结构作为【整体】参与深度排序的地面基准线 Y（下缘）。

        默认 = 叶子世界包围盒下缘。子类应覆写成"纯几何、不依赖
        world_position/get_sprite_order"的算法，因为 world_position 会经
        get_sprite_order → _iter_sprites_with_depth 回环遍历叶子。
        """
        r = self.world_position
        return float(r.bottom) if r.height else 0.0

    def leaf_base_y(self, leaf) -> float:
        """某片叶子所站的【地面线】Y（不含 layer_offset）。

        默认 = 本结构整体的地面基准线（立面语义：整面共用一条）。
        结构内部若混有不同地面语义的格子（如竖墙的沿地面段 vs 侧立面段），
        由子类覆写本方法逐格/逐行给出。
        """
        return self.depth_base_y()

    def leaf_depth(self, leaf) -> float:
        """某片叶子参与【全局】深度排序的深度值 = leaf_base_y(leaf) + layer_offset。

        为什么共享基准线而不是各格用自己的下缘：墙在 2.5D 里是「立面」，其各行
        只是立面上不同的高度，不该各自拿自身下缘去和外界穿插排序 —— 否则一堵高
        前墙的上半截会排到屋内角色身后，出现"半堵墙在人身前、半堵墙在身后"的破图。
        叶子之间的相对次序由 get_sprite_order()（结构内部排序）决定。
        """
        return self.leaf_base_y(leaf) + float(self.layer_offset or 0)

    # ---- 遮挡交互（被判为"挡住焦点实体"之后做什么） ----
    #
    # 分工：**判定**是纯几何自动的（layer_v2.occluders_of：更近 + 实际相交），
    # 这里只**声明策略**，渲染层再决定怎么应用。
    #
    # 默认 FADE = 通用规则："谁挡住焦点谁淡到半透明，让玩家看见里面"。
    # 不想退的结构显式写 NONE（opt-out），例如从侧面看也必须完整的装饰物。
    # 判定仍然只在"真的挡住"时触发（更近 且 像素实际相交），所以默认值本身
    # 不会让画面在没遮挡时发生任何变化。
    occlusion_policy = OcclusionPolicy.FADE

    def occlusion_policy_for(self, leaf, focus=None) -> OcclusionPolicy:
        """某片叶子被判为遮挡者时采用什么策略（默认整结构一条）。

        子类可按叶子给不同策略，例如某面墙永不淡出、另一面剖视。
        """
        return self.occlusion_policy

    def occlusion_owner_of(self, leaf):
        """某片叶子淡出时【以谁为单位】。

        默认就是本结构 —— 整块一起淡。
        组合结构必须覆写成本片叶子所属的【部件】：例如 House 的四面墙要各自
        独立淡出（挡住玩家的那面墙退开，其他墙纹丝不动），而不是整栋房子一起淡。
        见 House.occlusion_owner_of。
        """
        return self

    @property
    def centerx(self) -> float:
        """世界像素中心 X（由子类提供；默认从 dock 几何推断，无则 0）。"""
        dock = self.get_dock_geometry()
        if dock is not None:
            return dock.cx
        return 0.0

    @property
    def center(self):
        return

    @property
    def world_position(self) -> pygame.Rect:
        """整墙在世界的包围盒（只读，随叶子实时计算）。

        Structure 通常由多个格子精灵组成，本身不存单一位置；这里返回
        所有叶子 world_position 的外包矩形，供编辑器/空间网格/block 激活
        把它们当一个整体对象处理（LAYER_SYSTEM_V2_DESIGN §7）。无叶子时返回空矩形。
        """
        leaves = [s for s in self._iter_leaf_sprites()
                  if getattr(s, "world_position", None) is not None]
        if not leaves:
            return pygame.Rect(0, 0, 0, 0)

        def gx(o, attr, default=0.0):
            v = getattr(o, attr, None)
            return float(v) if v is not None else default

        ws = [s.world_position for s in leaves]
        left = min(gx(o, "x") for o in ws)
        top = min(gx(o, "y") for o in ws)
        right = max(gx(o, "x") + gx(o, "width") for o in ws)
        bottom = max(gx(o, "y") + gx(o, "height") for o in ws)
        return pygame.Rect(left, top, right - left, bottom - top)

    def get_sprite_order(self) -> list[pygame.sprite.Sprite]:
        """叶子按深度排序（缓存；几何变更时经 _invalidate_order 置空）。

        优化：大量 Structure 时，每帧对整墙叶子重新排序很贵；静态墙叶子顺序稳定，
        缓存到几何变更前即可复用。
        """
        if self._order_cache is None:
            items = list(self._iter_sprites_with_depth())
            items.sort(key=lambda item: item[1])
            self._order_cache = tuple(sprite for sprite, _ in items)
        return list(self._order_cache)

    def _invalidate_order(self) -> None:
        """叶子/几何变更后调用：清空排序缓存。"""
        self._order_cache = None

    def iter_sorted_leaf_sprites(self) -> list:
        """绘制契约（LAYER_SYSTEM_V2_DESIGN §6/§7）：Structure 是组合对象，
        Layer 把它当一个整体成员；渲染器经此展开其内部按深度排好的精灵。"""
        return self.get_sprite_order()

    def _iter_sprites_with_depth(self):
        raise NotImplementedError

    def update(self):
        raise NotImplementedError

    # ================= 连接相关（范式 v3：连接单元 = 端口） =================
    #
    # 注意：连接不是 结构↔结构，而是 端口↔端口。
    # 需要建立连接时，到本 structure 上取具体 Port（get_side_ports / get_all_ports /
    # iter_all_ports / ports_state），然后调用 port_a.connect_to(port_b)。
    # Structure 只负责：集中持有/暴露端口 + 在连接后把相关 sprite 取出并传连接方向给
    # sprite 让其自决换图。这里不再提供 connect_to(另一结构) 的入口。

    # ---- 形状几何（子类实现） ----
    def get_dock_geometry(self):
        """返回 tiledshape.RectDock 之类的几何描述；未实现返回 None。"""
        return None

    # ---- 边界端口（子类声明 Port 节点类型） ----
    def port_node_for_cell(self, cell: tuple[int, int]):
        """
        声明某个边界格(cell)上挂哪个 Port 节点子类。
        返回 None = 该格没有端口（接触线上此格不参与连接）。
        默认基类不挂任何口 —— 具体由子类（如 HorizontalWall 的四角）覆写。

        （保留旧名 port_class_for 作为别名，但仍以 cell 为准，侧边信息由节点自身
        的 connectable_sides 声明。）
        """
        return None

    def port_class_for(self, side: str, index: int, cell: tuple[int, int]):
        """兼容别名：按 cell 返回节点类型（旧签名已废弃，方向由 connectable_sides 声明）。"""
        return self.port_node_for_cell(cell)

    def _node_port_for_cell(self, cell: tuple[int, int]):
        """取/建某个边界格的多向端口节点（每格一个，懒生成并缓存）。"""
        if cell not in self._cell_ports_cache:
            cls = self.port_node_for_cell(cell)
            self._cell_ports_cache[cell] = cls(self, None, cell) if cls is not None else None
        return self._cell_ports_cache[cell]

    def get_side_ports(self, side: str) -> list:
        """
        取某条边按“沿边序号”排列的端口节点列表（无端口处为 None），懒生成并缓存。

        端口节点 = 一个边界格一个（多向）。同一个角格（例如横墙四角）同时属于
        top/left 两条边，但只有【一个】节点实例；本方法在两条边上都返回同一个节点。
        方向连接（用哪条边）由 connect_to 的 self_side/peer_side + 节点 connectable_sides 决定。
        """
        dock = self.get_dock_geometry()
        if dock is None:
            raise NotImplementedError(
                f"{type(self).__name__} 未实现 get_dock_geometry()，无法生成端口。")
        cells = dock.boundary_cells(side)
        ports = []
        for i, cell in enumerate(cells):
            p = self._node_port_for_cell(cell)
            if p is not None:
                p.index = i          # 记录沿该边序号（观测用；多边格保留首次值）
            ports.append(p)
        return ports

    def get_all_ports(self) -> list:
        """取本结构所有边界格上的全部端口节点（去重，每格一个）。"""
        return [p for p in self._cell_ports_cache.values() if p is not None]

    # ---- 语义命名端口（可读性：structure.ports["top_left"] 直接拿到 port） ----
    def port_name_for(self, side: str, index: int, cell: tuple[int, int]):
        """
        给某条边第 index 个边界格(cell)的端口起一个【语义名】，作为 ports 字典的 key。
        返回 None = 不收录进 ports 字典（无口处、或不想暴露的边）。
        默认不命名；子类（横墙四角名、竖墙段名）覆写。
        """
        return None

    @property
    def ports(self) -> dict:
        """
        语义命名端口字典：{name: Port}。取代按「side + 下标」取口的可读性差的写法。

        只收录各边上有名称且非 None 的端口；同一格的端口若在多个语义名下暴露，
        取首个登记名（避免字典 key 冲突）。用于拿具体端口直接 connect_to。
        """
        result = {}
        for side in ("top", "bottom", "left", "right"):
            ports = self.get_side_ports(side)
            for i, p in enumerate(ports):
                if p is None:
                    continue
                name = self.port_name_for(side, i, p.cell)
                if name is None:
                    continue
                if name in result:
                    continue
                result[name] = p
        return result

    def port(self, name: str) -> "Port":
        """按语义名取端口；不存在时抛 KeyError（比 get_side_ports 的下标写法可读）。"""
        return self.ports[name]

    def port_at_side(self, side: str, cell: tuple[int, int]):
        """取某条边上、位于给定边界格(cell)的端口节点；该格该边无口则返回 None。

        用于「拿到语义端口后手动指定连接 side」：ports[name] 只代表位置，
        具体用哪一侧(左/右)去连，由调用方在 connect_to(peer, self_side=...) 里指定。
        """
        p = self._node_port_for_cell(cell)
        if p is not None and side in p.connectable_sides:
            return p
        return None

    def _marker_world_rect(self, port: "Port"):
        """取某个 Port 所在格在世界坐标系下的矩形（供渲染叠加层 DPM）。

        两级解析（避免「宿主结构声明了端口却画不出标记」的静默失败）：
        1) 优先用该格精灵的 world_position —— 与已绘制网格精确对齐
           （有纹理实体、已 parse_and_register 的结构走这条）；
        2) 该格没注册精灵（无纹理壳 / 未注册 / 组合结构部件）时，退回用
           「格中心 + 格尺寸」直接从 dock 几何计算世界矩形，
           保证任何声明了端口的宿主结构必然能产出标记，而不是被静默跳过。
        无法解析时返回 None（调用方记录诊断，见 get_port_markers）。
        """
        # 1) 精灵路径（精确对齐）
        try:
            sprite = self._sprite_at_port(port)
            if sprite is not None:
                wp = getattr(sprite, "world_position", None)
                if wp is not None and getattr(wp, "width", 0) > 0 and getattr(wp, "height", 0) > 0:
                    return pygame.Rect(int(wp.left), int(wp.top),
                                       int(wp.width), int(wp.height))
        except Exception:
            pass
        # 2) 几何回退：格中心 +/- 格尺寸（用于无纹理壳 / 组合部件）
        try:
            cx, cy = port.cell_center_world()
            cw = float(getattr(self, "_cell_w", 64) or 64)
            ch = float(getattr(self, "_cell_h", 64) or 64)
            if cw <= 0 or ch <= 0:
                cw = ch = 64.0
            return pygame.Rect(cx - cw / 2.0, cy - ch / 2.0, cw, ch)
        except Exception:
            return None

    def get_port_markers(self) -> list:
        """
        供渲染器叠加层使用的端口标记查询（纯数据，不修改任何精灵）。
        返回 [(world_rect, state), ...]，**按世界矩形聚合**：
        - state = 该格端口的标记状态，取所有边口中最大者：
            0=空闲(绿) / 1=已连接未满(黄) / 2=已满·占用(红)。
        - world_rect : 该格在世界坐标系下的矩形（供世界→屏幕换算）。

        端口所在格即便没有注册精灵（无纹理壳 / 组合部件），也会经
        :meth:`_marker_world_rect` 的回退几何算出矩形 —— 见该方法注释。
        """
        # world_rect -> [wp, state]（按世界矩形去重；同格跨边口取最高状态）
        by_rect = {}
        for side in ("top", "bottom", "left", "right"):
            ports = self.get_side_ports(side)
            for p in ports:
                if p is None:
                    continue
                wp = self._marker_world_rect(p)
                if wp is None:
                    self._warn_port_marker_unresolved(p)
                    continue
                key = (int(wp.left), int(wp.top), int(wp.width), int(wp.height))
                cur = by_rect.get(key)
                state = getattr(p, "marker_state", (0 if p.is_free else 1))
                if cur is None:
                    by_rect[key] = [wp, state]
                elif state > cur[1]:
                    cur[1] = state   # 任一口更高状态 → 取高者（满→占用红优先）
        return [list(v) for v in by_rect.values()]

    # Mutable 诊断：结构上有端点但画不出标记时记一次，避免静默失败（预防此类 bug）
    _resolved_no_marker_cells: set = None  # 惰性初始化，防类级共享

    def _warn_port_marker_unresolved(self, port: "Port") -> None:
        """某个端口既无精灵、几何回退也失败时，一次性告警（按 (host_id, cell) 去重）。"""
        try:
            if getattr(self.__class__, "_resolved_no_marker_cells", None) is None:
                self.__class__._resolved_no_marker_cells = set()
            key = (id(self), port.cell)
            if key in self.__class__._resolved_no_marker_cells:
                return
            self.__class__._resolved_no_marker_cells.add(key)
            import warnings
            warnings.warn(
                f"{type(self).__name__}#{self.instance_id} 的端口 {port.cell} "
                f"既无精灵也无几何回退，无法绘制 DPM 标记(host 结构未实现 "
                f"_sprite_at_port/dock 几何)。", UserWarning, stacklevel=3)
        except Exception:
            pass

    def move_by(self, dx: float, dy: float):
        """子类实现：把整体平移到新位置（含内部精灵）。"""
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 move_by()，无法作为主动方移动。")

    def _decorate_leaf(self, sprite) -> None:
        """叶子内容可能被改过之后的收口钩子（默认**什么都不做**）。

        给子类一个统一的机会去"重新装饰"某个叶子 —— 例如墙体按自己声明的侧面规则
        给格子补上侧脸（`side_face`）。

        换图只有一条路径（读 `wall_texture_plan()` → `sprite.apply_frame(帧号, 翻转)`），
        本钩子只做"贴图之外"的收口，不参与决定该贴哪张图。

        默认实现是空操作：不覆写就 100% 维持原有画面与行为，不会引入隐式变化。
        调用点见 WallGridMixin.register_sprite / update，以及结构 on_port_changed。
        """
        return None

    def on_port_changed(self, port_a: "Port", port_b: "Port",
                        side_a: str, side_b: str, *, connected: bool) -> None:
        """一对端口刚连上 / 刚断开时的【逐对】回调（本端是 port_a 的宿主）。

        设计理念（2026 定稿）：
        - 连接位置可以是墙体的任意部位（Port 挂在哪个格，哪里就能连），不限于角/顶格；
        - “换什么纹理”由被连接的那一格精灵(port 所在格)自己决定：
          Structure 在此取出 `port_a` 所在格 sprite，把【连接方向】随回调传给精灵，
          精灵知道自己是连向左还是连向右，自行切换贴图（且可能引发整列连锁更换）；
        - 端口色标是渲染特效，不在这里画 —— 渲染器叠加层会读取 Port 状态。

        :param connected: True = 刚连上，False = 刚断开。

        **本方法不做整墙重算** —— "按整批拓扑重算"统一由
        :meth:`on_topology_settled` 承担（引擎在事务结束时调用，每结构一次）。
        所以默认实现只在"连上"时把方向交给本格精灵收口；断开什么都不用做
        （贴图由随后的 settled 重算）。只改单格的子类可以直接覆写本方法。
        """
        if not connected:
            return
        sprite = self._sprite_at_port(port_a)
        if sprite is None:
            return
        direction = self._connection_direction(port_a, port_b)
        on_dock = getattr(sprite, "on_dock", None)
        if callable(on_dock):
            on_dock(port_a, port_b, direction)   # 换纹理的最终决定权在格子精灵自己
        # 精灵换完图后的收口：结构可在此按自己的规则重新装饰叶子（默认无操作）
        self._decorate_leaf(sprite)

    def _connection_direction(self, port_a: "Port", port_b: "Port") -> str:
        """判断本端(port_a)相对对端(port_b)的连接方向。

        优先用两结构中心 X 比较判左右；中心重合（叠放）时按两端所在边缘判上下。
        返回 "left"/"right"/"top"/"bottom"（相对本端朝对端的方向）。
        """
        ha = getattr(port_a, "structure", None)
        hb = getattr(port_b, "structure", None)
        if ha is not None and hb is not None:
            ax, bx = getattr(ha, "centerx", 0), getattr(hb, "centerx", 0)
            if abs(bx - ax) > 1e-6:
                return "right" if bx > ax else "left"
            # 中心重合：按两端所在边（本端边 + 对端边互补）推断
        # 退回：按本端所在边缘给方向（port_a.side）
        map_ = {"top": "bottom", "bottom": "top",
                "left": "right", "right": "left"}
        return map_.get(getattr(port_a, "side", "right"), "right")

    def _sprite_at_port(self, port: "Port"):
        """取某个 Port 所在边界格上的精灵（无则 None）。由子类按自己的网格结构实现。"""
        return None

    def record_connection(self, other):
        """互相记录“谁连了谁”（Structure 实例级）。"""
        if other not in self.connections:
            self.connections.append(other)

    def iter_all_ports(self):
        """遍历本 structure 已生成的全部端口节点（每格一个，含空闲与已连）。"""
        for p in self._cell_ports_cache.values():
            if p is not None:
                yield p

    def connected_ports(self) -> list:
        """已连接(非空闲)的端口列表。"""
        return [p for p in self.iter_all_ports() if not p.is_free]

    def on_topology_settled(self):
        """整批连接/断开完成后由连接引擎一次性调用（纯调度语义入口）。

        子类在此读 connected_ports() 后刷新自身所有格。
        默认空实现。

        时机保证（见 `Port.connect_to` / `Port.disconnect`）：一次连接事务里，
        级联移动过的结构也会收到本回调，且每个结构在一次事务里**最多收到一次**。
        """
        pass

    def __repr__(self):
        return (f"{type(self).__name__}(type={self.structure_type!r}, "
                f"id={self.instance_id!r})")
