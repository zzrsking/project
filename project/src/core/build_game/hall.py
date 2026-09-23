"""hall.py —— 走廊（Hall）：house 与 house 之间的连接部分。

走廊 = **房子拆掉一个轴向上的两道墙**。所以它天然有两种朝向（orientation）：

    orientation="ns"（南北向 / 竖向连廊）
        地板 + 左右两道【竖墙】；南北两端开口 —— 人沿 Y 轴走。
        开口两侧就是竖墙的头/尾，纹理自然是"自由端"(33 / 45·55·63)。

    orientation="ew"（东西向 / 横向连廊）
        地板 + 上下两道【横墙】；东西两端开口 —— 人沿 X 轴走。
        与房子前/后墙同款摆放（下墙走重叠口，压在地板南缘之上）。

两种朝向的部件摆放**完全交给已有的端口吸附**（与 RectangleHouse 同一套调用），
不再手工算 offset —— 旧实现就是手工摆"几字形门框"摆错了：
    · 门框横墙落在 ty = cy ± width*cell/2（地板端行的中心），于是压住地板的内侧行；
    · 门框立柱落在 px = cx ± span*cell/2（地板的边列），和走廊自己的两根竖墙同列重叠。
两者叠在一起，看起来就是"贴图错乱、墙重复、走廊内部被墙吃掉"。
因此 `with_edges` 只保留参数兼容，不再生成门框（见 build 文档）。

端口的连接点
------------
走廊的两端各挂一个开口端面端口（见 opening.py 的 OpenEdgePort），方向是"沿走廊轴向"：

    ns 走廊：north / south 口 connectable_sides=("top","bottom")
    ew 走廊：west  / east  口 connectable_sides=("left","right")

于是：
    · 连廊 <-> 连廊：(OpenEdge, OpenEdge) 一条规则 → 同轴串联或 L 形拐角；
    · 连廊 <-> 房子：房子先 open_side(某侧) 拆墙得到开口，再接（同一个端口类型）。
宽度/格尺寸不一致时用 connect_to(..., geometry="loose") 走非几何检测路径。

接头纹理（junction）
--------------------
NS 走廊的竖墙在 exact 连接后【墙列完全重合】——下面那条走廊的墙头格（row 0）
正好压在上面那条的墙尾/正面格上。墙头本来画的是"自由端收口"（33），
于是连成一气的墙中间会突兀地冒出一个墙头。

所以：**开口被接上的一端，墙头改用 top_run 的贯通纹（16/18）**。
实现见 Hall.sync_junctions()：它由 `on_topology_settled()`（一次连接/断开事务结束时
每结构调一次）自动跑，也可以显式调用；底层落到 `VerticalWall._resolve_run()` →
`vertical_texture.vertical_cell_frame(head_run=True)`。不接的时候一切照旧（回退自由端纹）。

房子那边「一条边接若干走廊」的做法见 house_edge.py（拆边 → 单格口 → 补墙小段）。
"""

from src.core.build_game.structure import RenderOrder
from src.core.build_game.house import House, _apply_settings_side_face
from src.core.build_game.horizontalwall import HorizontalWoodWall, HorizontalWall
from src.core.build_game.verticalwall import (VerticalWoodWall, VerticalWall,
                                             register_wall_family_corner_rules)
from src.core.build_game.floor import BrickFloor, Floor, register_floor_corner_rules
from src.core.build_game.port import ConnectError, register_rule, RULES
from src.core.build_game.opening import (OpenEdgePort, OPEN_EDGE_TYPE, OUTWARD,
                                         make_edge_ports, register_opening_rules)


NS = "ns"      # 南北向（竖向连廊）：竖墙在左右，南北开口
EW = "ew"      # 东西向（横向连廊）：横墙在上下，东西开口
ORIENTATIONS = (NS, EW)

# 端口类型：开口端面（"非几何"友好 —— 只声明轴向，不声明具体角词汇）
HALL_EDGE_TYPE = OPEN_EDGE_TYPE
_OUTWARD = OUTWARD

# 兼容旧名（此前 hall.py 内自定义的端口类）
HallEdgePort = OpenEdgePort


class Hall(House):
    """走廊：地板 + 一个轴向上的两道墙，另一个轴向两端开口。"""

    structure_type = "hall"
    is_occluder = True

    # ------------------------------------------------------------------ #
    # 一步构建
    # ------------------------------------------------------------------ #
    @classmethod
    def build(cls, length: int, width: int, height: int = 3,
              cell: tuple = (64, 64),
              center: tuple[float, float] = (0, 0),
              use_wood: bool = True,
              orientation: str = NS,
              with_edges: bool = False,
              edge_double_min: int = 3) -> "Hall":
        """构建一条走廊。

        :param length: 长边格数（ns 时沿 X 为"宽度跨度"，ew 时沿 X 就是走廊长度）
        :param width:  短边格数（地板进深）
        :param height: 墙高（行数）
        :param orientation: "ns"（竖墙在左右、南北开口）/ "ew"（横墙在上下、东西开口）
        :param with_edges: 兼容旧参数。旧实现的"几字形门框"手工摆位是错的
                           （压地板端行 + 立柱与侧墙同列重叠），现已不再生成门框；
                           传 True 只记一条 build_log，不改变几何。
        """
        if length < 2 or width < 2 or height < 2:
            raise ValueError(
                f"尺寸需 >=2（引擎不允许 N×1 格）："
                f"length={length} width={width} height={height}")
        orientation = str(orientation).lower()
        if orientation not in ORIENTATIONS:
            raise ValueError(f"orientation 必须是 {ORIENTATIONS} 之一，收到 {orientation!r}")

        length, width, height = int(length), int(width), int(height)
        cell = tuple(cell)
        cx, cy = center
        log: list[str] = []
        _ensure_rules(log)

        # ---- 地板 ----
        if use_wood:
            floor = BrickFloor((length, width), center)
        else:
            floor = Floor((length, width), cell, center)

        # ---- 两道侧墙：摆放与 RectangleHouse 完全一致，位置由端口吸附定死 ----
        walls = {}
        if orientation == NS:
            # 左右竖墙（长度 = 进深+1，与房子侧墙同款）
            for role, sign in (("left", -1), ("right", 1)):
                wx = cx + sign * (length * cell[0] / 2)
                wall = (VerticalWoodWall(width + 1, height, center=(wx, cy))
                        if use_wood else
                        VerticalWall(width + 1, height, cell, (wx, cy)))
                walls[role] = wall
        else:
            # 上下横墙（跨度 = length，下墙挂可重叠口 -> 与房子前墙同款压在地板南缘）
            for role, sign in (("top", -1), ("bottom", 1)):
                wy = cy + sign * (width * cell[1] / 2)
                wall = (HorizontalWoodWall((length, height), (cx, wy),
                                           overlap_bottom=(role == "bottom"))
                        if use_wood else
                        HorizontalWall((length, height), cell, (cx, wy),
                                       overlap_bottom=(role == "bottom")))
                walls[role] = wall

        # ---- 端口连接（吸附对齐） ----
        if orientation == NS:
            # 与旧 Hall / 房子的竖墙落位一致：地板下角 <-> 竖墙底口
            _try(walls["left"].port("bottom"), floor.port("bottom_left"),
                 "right", "left", "left_wall.bottom <-> floor.bottom_left", log)
            _try(walls["right"].port("bottom"), floor.port("bottom_right"),
                 "left", "right", "right_wall.bottom <-> floor.bottom_right", log)
        else:
            # 与房子的前/后墙落位一致：下墙走重叠口，上墙走普通下角口
            _try(walls["bottom"].port("bottom_left"), floor.port("bottom_left"),
                 "bottom", "bottom", "bottom_wall.bottom_left <-> floor.bottom_left (重叠)", log)
            _try(walls["bottom"].port("bottom_right"), floor.port("bottom_right"),
                 "bottom", "bottom", "bottom_wall.bottom_right <-> floor.bottom_right (重叠)", log)
            _try(walls["top"].port("bottom_left"), floor.port("top_left"),
                 "bottom", "top", "top_wall.bottom_left <-> floor.top_left", log)
            _try(walls["top"].port("bottom_right"), floor.port("top_right"),
                 "bottom", "top", "top_wall.bottom_right <-> floor.top_right", log)

        hall = cls(floor,
                   walls.get("left"), walls.get("right"),
                   top_wall=walls.get("top"), bottom_wall=walls.get("bottom"))
        hall.params = {
            "length": length, "width": width, "height": height,
            "cell": cell, "center": tuple(center), "use_wood": bool(use_wood),
            "orientation": orientation, "with_edges": bool(with_edges),
        }
        hall.build_log = list(log)
        if with_edges:
            hall.build_log.append(
                "[WARN] with_edges 已废弃：旧几字形门框摆位错误（压地板端行/立柱与侧墙同列），"
                "本次不生成门框。")
        hall._install_edge_ports(cell, orientation)
        hall.sync_junctions()
        _apply_settings_side_face(hall)
        return hall

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #
    def __init__(self, floor, left_wall, right_wall, *,
                 top_wall=None, bottom_wall=None,
                 layer_offset: int = RenderOrder.BOTTOM.value):
        self._edge_ports = {}
        self._junction_marks = {}      # 手工声明：{端别: 是否算"已接上"}
        self._junction_run = False     # 最近一次同步的结果（北/西端的墙头是否跑通）
        self._cell_w = getattr(floor, "_cell_w", 64)
        self._cell_h = getattr(floor, "_cell_h", 64)
        self.orientation = NS
        super().__init__(floor, top_wall, bottom_wall, left_wall, right_wall,
                         layer_offset=layer_offset)

    # ---- 端面端口 ----
    def _install_edge_ports(self, cell, orientation):
        """两端各装一个开口端面端口（锚定格 = 地板在该端的中间格）。"""
        self.orientation = orientation
        self._cell_w, self._cell_h = cell
        floor = self._parts.get("floor")
        ends = (("north", "south") if orientation == NS else ("west", "east"))
        self._edge_ports = make_edge_ports(self, floor, ends)

    # ---- 端口视图：房子部件的具名端口 + 走廊两端开口 ----
    # House 自身没有 `ports` 属性（组合结构走 dock 几何会抛 NotImplementedError），
    # 所以这里自己聚合：不依赖 house.py 提供 ports。
    @property
    def ports(self) -> dict:
        out = {}
        for name, part in self._parts.items():
            part_ports = getattr(part, "ports", None)
            if isinstance(part_ports, dict):
                for pname, port in part_ports.items():
                    out[f"{name}:{pname}"] = port
        out.update(getattr(self, "_edge_ports", {}) or {})
        return out

    def port(self, name: str):
        """端口入口：先看走廊两端的开口端面，再回落到 House 的部件端口。"""
        edge = getattr(self, "_edge_ports", {}) or {}
        if name in edge:
            return edge[name]
        return super().port(name)

    def edge_ports(self) -> dict:
        """{端别: OpenEdgePort}，端别 ∈ north/south（ns）或 west/east（ew）。"""
        return dict(getattr(self, "_edge_ports", {}) or {})

    # ------------------------------------------------------------------ #
    # 接头（junction）纹理：开口被接上的一端，墙头不再画"自由端"
    # ------------------------------------------------------------------ #
    # 为什么需要：NS 走廊的两道竖墙是"一根"墙 —— 墙头格（row 0，最北）画的是
    # 自由端收口纹（33）。两段走廊南北串起来时，下面那条走廊的墙头正好叠在上面
    # 那条的墙尾/正面格上（exact 连接后锚定格相邻，墙列完全重合），于是连成一气的
    # 墙中间会突兀地冒出一个"墙头"。把墙头格切成 top_run 纹（16/18）之后，
    # 上下两段的墙身纹就接上了，看起来是一堵完整的墙。
    def _junction_ends(self) -> set:
        """已经"接上东西"的端别集合：开口真的连了端口，或被 mark_junction 手工声明。"""
        joined = set()
        for end, port in self.edge_ports().items():
            if port is not None and port.peers:
                joined.add(end)
        for end, val in (getattr(self, "_junction_marks", {}) or {}).items():
            if val:
                joined.add(end)
            else:
                joined.discard(end)      # 手工取消：即使真连着也当没接
        return joined

    def mark_junction(self, end: str = None, joined: bool = True):
        """手工声明某个开口端算不算"已接上"（不依赖实际端口连接）。

        :param end: 端别（north/south/west/east）；None = 全部端
        :param joined: True = 该端当作已接上（墙头改跑通纹）/ False = 取消
        """
        ends = list(self.edge_ports()) if end is None else [str(end).lower()]
        for e in ends:
            self._junction_marks[e] = bool(joined)
        return self.sync_junctions()

    def sync_junctions(self, *, log=None):
        """同步"接头"：① 墙-墙端口按既有规则连起来；② 各墙按新拓扑重算贴图。

        NS 走廊：墙头在北端。north 接上了 → 左右两道竖墙的头格按"贯通"处理
        （上面还接着墙 → 不画自由端收口 33；共线续接 → 不画转角纹，画 16/18）。
        EW 走廊：墙是横墙（没有"头格贯通"这套语义），只做①的端口连接。

        幂等：连接 / 断开 / 显式调用跑多次都安全（端口满了会 SKIP，不会重复连）。
        """
        from src.core.build_game.wall_joint import join_hall_neighbours
        joined = self._junction_ends()
        marks = getattr(self, "_junction_marks", {}) or {}
        # ① 墙与墙：相接的墙端连起来（共线 → 贯通；成角 → 转角纹，走既有逻辑）
        joints = join_hall_neighbours(self, log=log)
        # ② 开口接上了（但那一端未必有墙端 partner）→ 也按贯通处理
        if self.orientation == NS:
            for name in ("left_wall", "right_wall"):
                wall = self._parts.get(name)
                note = getattr(wall, "note_opening_attached", None)
                if not callable(note):
                    continue
                for end, key in (("head", "north"), ("tail", "south")):
                    if key in marks:
                        note(end, False)            # 手工声明优先，见 ③
                    else:
                        note(end, key in joined)
                clear = getattr(wall, "clear_run_overrides", None)
                if callable(clear):
                    clear()
        # ③ 手工声明（mark_junction）最后压上去：
        #    True  = 强制按贯通处理（覆盖自动判定）；
        #    False = 明确当"没接上"：把这一端的墙-墙接头拆掉 → 恢复自由端纹。
        for end, val in marks.items():
            key = {"north": "head", "south": "tail"}.get(end)
            if key is None:
                continue
            for name in ("left_wall", "right_wall"):
                wall = self._parts.get(name)
                if wall is None:
                    continue
                if val:
                    setter = "set_head_run" if key == "head" else "set_tail_run"
                    fn = getattr(wall, setter, None)
                    if callable(fn):
                        fn(True, force=True)
                    continue
                port_name = "top" if key == "head" else "tail"
                port = (getattr(wall, "ports", {}) or {}).get(port_name)
                if port is not None and not port.is_free:
                    try:
                        port.disconnect()
                    except Exception:      # noqa: BLE001
                        pass
                clear_note = getattr(wall, "note_inline", None)
                if callable(clear_note):
                    clear_note(key, None)
                clear = getattr(wall, "clear_run_overrides", None)
                if callable(clear):
                    clear()
        # ④ 让每堵墙按当前端口 / 邻居记录重算贴图
        for name in ("left_wall", "right_wall", "top_wall", "bottom_wall"):
            refresh = getattr(self._parts.get(name), "refresh_runs", None)
            if callable(refresh):
                refresh()
        run = any(bool(getattr(self._parts.get(n), "_head_run", False))
                  for n in ("left_wall", "right_wall"))
        self._junction_run = run
        return run

    # 端口簇变化只有一条重算入口：on_topology_settled（引擎在事务结束时调用一次）
    def on_topology_settled(self):
        self.sync_junctions()
        return super().on_topology_settled()

    # ------------------------------------------------------------------ #
    # 自省
    # ------------------------------------------------------------------ #
    @property
    def axis(self) -> str:
        """走廊轴向："y"（南北）或 "x"（东西）—— 也就是"走人的方向"。"""
        return "y" if self.orientation == NS else "x"

    @property
    def openness(self) -> tuple:
        """开口所在的两端。"""
        return ("north", "south") if self.orientation == NS else ("west", "east")

    def describe(self) -> str:
        p = getattr(self, "params", {}) or {}
        axis = "南北向(竖墙在左右)" if self.orientation == NS else "东西向(横墙在上下)"
        return (f"Hall(length={p.get('length', '?')}, width={p.get('width', '?')}, "
                f"height={p.get('height', '?')}, {axis}, 开口={self.openness})")

    def debug(self) -> str:
        lines = [f"Hall debug: {self.describe()}", "  ---- 部件 ----"]
        for name, part in self._parts.items():
            if part is None:
                continue
            gw = getattr(part, "_grid_width", "?")
            gh = getattr(part, "_grid_height", "?")
            lines.append(f"  {name:22s} {type(part).__name__:20s} grid=({gw},{gh})")
        lines.append("  ---- 开口端面 ----")
        for end, port in self.edge_ports().items():
            lines.append(f"  {end:8s} cell={port.cell} sides={port.connectable_sides} "
                         f"status={port.status}")
        lines.append("  ---- 接头 ----")
        joined = sorted(self._junction_ends())
        lines.append(f"  已接上的端={joined or '无'}  墙头跑通={bool(self._junction_run)}")
        lines.append("  ---- 连接观测 ----")
        for line in getattr(self, "build_log", []):
            lines.append(f"    {line}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Hall({self.describe()})"


# ======================================================================
# 规则 / 工具
# ======================================================================
def register_hall_rules(log=None, overwrite: bool = True):
    """注册走廊/开口相关连接规则（幂等）。

    · OpenEdge <-> OpenEdge   : 开口接开口（连廊<->连廊、连廊<->拆了墙的房子）
    · OpenEdge <-> 墙族四角口 : 直接接在某个**空闲**的墙角口上（房子自身占满时不可用）
    """
    out = []
    register_opening_rules(log=log)
    pairs = [
        (HALL_EDGE_TYPE, "HorizontalWallBottomLeft"),
        (HALL_EDGE_TYPE, "HorizontalWallBottomRight"),
        (HALL_EDGE_TYPE, "HorizontalWallTopLeft"),
        (HALL_EDGE_TYPE, "HorizontalWallTopRight"),
    ]
    for a, b in pairs:
        existed = RULES.get(a, b)
        if existed is not None:      # 规则表保护：同一类型对只注册一次
            out.append((a, b, existed))
            continue
        rule = register_rule(a, b, action="hall_join")
        out.append((a, b, rule))
        if log is not None:
            log.append(f"[RULE] {a} <-> {b}")
    return out


def _ensure_rules(log=None):
    """墙族四角规则 + 地板角规则 + 开口规则（幂等，重复注册安全跳过）。"""
    for _r in register_wall_family_corner_rules(log=log):
        pass
    for _r in register_floor_corner_rules(log=log):
        pass
    register_hall_rules(log=log)


def _ascii(text) -> str:
    """把 Windows GBK 控制台打不出来的字符换成 ASCII 等价物。

    `'↔'` 不在 GBK 里（`'↔'.encode('gbk')` 直接抛错），而 PortConnectResult.summary()
    里就有它 —— 一旦这些日志被 print 出来，GBK 控制台会 UnicodeEncodeError 崩掉。
    这里只在自己的日志出口消毒，不改公共 port.py 的字符串。
    """
    out = str(text)
    for src, dst in (("\u2194", "<->"), ("\u2192", "->"), ("\u2190", "<-"),
                     ("\u2265", ">="), ("\u2264", "<=")):
        out = out.replace(src, dst)
    try:
        out.encode("gbk")
    except UnicodeEncodeError:
        out = out.encode("gbk", "replace").decode("gbk", "replace")
    return out


def _try(a, b, self_side, peer_side, label, log):
    try:
        res = a.connect_to(b, self_side=self_side, peer_side=peer_side)
        log.append(_ascii(f"[OK] {label}  {res.summary()}"))
        return True
    except ConnectError as e:
        log.append(_ascii(f"[REJECT] {label}  {type(e).__name__}: {e}"))
        return False
    except Exception as e:      # noqa: BLE001
        log.append(_ascii(f"[ERR] {label}  {type(e).__name__}: {e}"))
        return False


def connect_halls(a: "Hall", b: "Hall", *, a_end: str = None, b_end: str = None,
                  geometry: str = "exact", log=None):
    """把两条走廊接起来（a 为主动方会被平移贴靠 b）。

    :param a_end/b_end: 指定 a、b 各自用哪个端面；缺省 a 的"正端"（south/east）
                        接 b 的"负端"（north/west）。
    :param geometry: "exact"（默认，按格宽做一格偏移，两格相邻）
                     / "loose"（非几何检测，锚定格重合，用于宽度不一致的拼接）
    """
    a_end = a_end or ("south" if a.orientation == NS else "east")
    b_end = b_end or ("north" if b.orientation == NS else "west")
    pa = a.port(a_end)
    pb = b.port(b_end)

    # 用哪条边：就是该端"朝外"的那条边（端别 -> 外边）。
    # 公式里 side_a 决定 a 落在 b 的哪一侧（bottom -> a 在上，top -> a 在下；
    # right -> a 在左，left -> a 在右），所以这里直接给外侧边即可。
    a_side = _OUTWARD[a_end]
    b_side = _OUTWARD[b_end]
    try:
        res = pa.connect_to(pb, self_side=a_side, peer_side=b_side, geometry=geometry)
        if log is not None:
            log.append(_ascii(f"[OK] hall {a_end}<->{b_end} ({geometry})  {res.summary()}"))
        return res
    except ConnectError as e:
        if log is not None:
            log.append(_ascii(f"[REJECT] hall {a_end}<->{b_end} ({geometry})  "
                              f"{type(e).__name__}: {e}"))
        return None
