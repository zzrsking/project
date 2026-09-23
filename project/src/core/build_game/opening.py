"""opening.py —— 结构【开口端面】端口（OpenEdge）。

为什么需要它
------------
房子/走廊的墙族角口（HorizontalWallTopLeft 之类）在**自身装配时就被占满了**
（底墙<->地板、竖墙尾<->底墙角…），所以"把走廊接到房子侧面"根本无口可用。
真正可用的接口是"**开口**"：一栋房子拆掉某面墙之后，那条边就是一个开口；
一条走廊两端本来就是开口。

所以开口端面单独做成一种端口：
  · 端口类型统一为 `OpenEdge` —— 不细分角词汇，所以"开口 <-> 开口"只需要一条规则；
  · 可连方向 = 该开口的**轴向**（南北开口 -> top/bottom；东西开口 -> left/right）；
  · 锚定格 = 开口所在边的中间格（组合结构里就是地板那格的精灵）。

于是：
    走廊 <-> 走廊、走廊 <-> 拆了墙的房子，全部是 (OpenEdge, OpenEdge) 一条规则搞定。
宽度/格尺寸不一致时，用 connect_to(..., geometry="loose") 走非几何检测路径。
"""

from src.core.build_game.port import Port, register_rule, RULES

OPEN_EDGE_TYPE = "OpenEdge"

# 端别 -> 该端"朝外"的边（连接时用哪条边）
OUTWARD = {"north": "top", "south": "bottom", "west": "left", "east": "right"}

# 端别 -> 可连方向（轴向）
SIDES_BY_END = {
    "north": ("top", "bottom"),
    "south": ("top", "bottom"),
    "west": ("left", "right"),
    "east": ("left", "right"),
}

# 侧别（房子用语）-> 端别
SIDE_TO_END = {"north": "north", "south": "south", "west": "west", "east": "east"}


class OpenEdgePort(Port):
    """开口端面端口：位于开口边中间格，沿开口轴向连接。

    - `end` 只做观测（north/south/west/east）；
    - `edge_type(side)` 一律回 OPEN_EDGE_TYPE —— 开口不分角词汇。
    """

    port_type = OPEN_EDGE_TYPE
    connectable_sides = ("top", "bottom")
    max_connections = 1

    def __init__(self, structure, index, cell, *, sides=None, end=None, host_part=None):
        super().__init__(structure, index, cell, connectable_sides=sides)
        self.end = end
        self.host_part = host_part        # 承载该开口的部件名（观测用，如 "floor"）

    def edge_type(self, side: str) -> str:
        return OPEN_EDGE_TYPE

    def cell_center_world(self):
        """锚定格世界中心：下沉到承载部件（地板）的网格去取那一格精灵。

        房子/走廊都是组合结构，基类 `_sprite_at_port` 返回 None，
        所以这里直接问地板部件要 —— 端口吸附就靠这个点对齐。
        """
        struct = getattr(self, "structure", None)
        parts = getattr(struct, "_parts", {}) or {}
        for name in (self.host_part, "floor"):
            part = parts.get(name) if name else None
            if part is None:
                continue
            getter = getattr(part, "_sprite_at_port", None)
            if callable(getter):
                sprite = getter(self)          # 只读 port.cell，跨部件安全
                wp = getattr(sprite, "world_position", None) if sprite is not None else None
                if wp is not None:
                    return (wp.centerx, wp.centery)
        return super().cell_center_world()

    def describe(self):
        return (f"OpenEdge[{self.end}] cell={self.cell} sides={list(self.connectable_sides)} "
                f"struct={type(self.structure).__name__}")


def make_edge_ports(struct, floor, ends):
    """给结构装上一组开口端面端口。

    :param struct: 宿主结构（House/Hall…组合结构）
    :param floor:  地板部件（锚定格从它的网格取）
    :param ends:   端别列表，如 ("north", "south") 或 ("west", "east")
    :return: {端别: OpenEdgePort}
    """
    gw = getattr(floor, "_grid_width", 2)
    gh = getattr(floor, "_grid_height", 2)
    out = {}
    for idx, end in enumerate(ends):
        if end in ("north", "south"):
            cell = (gw // 2, 0 if end == "north" else gh - 1)
        else:
            cell = (0 if end == "west" else gw - 1, gh // 2)
        out[end] = OpenEdgePort(struct, idx, cell,
                                sides=SIDES_BY_END[end], end=end, host_part="floor")
    return out


def register_opening_rules(log=None):
    """注册开口互连规则（幂等）：(OpenEdge, OpenEdge) —— 开口接开口。"""
    existed = RULES.get(OPEN_EDGE_TYPE, OPEN_EDGE_TYPE)
    if existed is None:
        rule = register_rule(OPEN_EDGE_TYPE, OPEN_EDGE_TYPE, action="opening_join")
        if log is not None:
            log.append(f"[RULE] {OPEN_EDGE_TYPE} <-> {OPEN_EDGE_TYPE}")
        return rule
    return existed


def connect_open_edges(active, passive, *, geometry: str = "exact", log=None):
    """把 active 的开口接到 passive 的开口上（active 为主动方，会被平移贴靠）。

    :param active/passive: OpenEdgePort
    :param geometry: "exact"（按格宽贴边、两格相邻）/ "loose"（非几何检测，锚定格重合）
    """
    a_side = OUTWARD[active.end]
    b_side = OUTWARD[passive.end]
    from src.core.build_game.port import ConnectError
    from src.core.build_game.hall import _ascii
    try:
        res = active.connect_to(passive, self_side=a_side, peer_side=b_side,
                                geometry=geometry)
        if log is not None:
            log.append(_ascii(f"[OK] opening {active.end}<->{passive.end} ({geometry})  "
                              f"{res.summary()}"))
        return res
    except ConnectError as e:
        if log is not None:
            log.append(_ascii(f"[REJECT] opening {active.end}<->{passive.end} ({geometry})  "
                              f"{type(e).__name__}: {e}"))
        return None


# ======================================================================
# 房子开口：从【外部】操作 House，不改 house.py
# ======================================================================
# 为什么必须"拆墙"才能接：房子的墙族角口在自身装配时就被占满了
# （底墙<->地板、竖墙尾<->底墙角…），端口是 max=1 的，外部根本没有空口可用。
# 所以接走廊前要先把那一侧的墙拆掉 —— 拆完那条边就是开口。
#
# 这些函数本来可以直接写成 House 的方法，但 house.py 是核心装配代码，
# 尽量不动它：开口是"上层用法"，放在这里以外部函数形式实现。

SIDE_TO_PART = {
    "north": "top_wall",
    "south": "bottom_wall",
    "west": "left_wall",
    "east": "right_wall",
}


def house_edge_ports(house) -> dict:
    """{端别: OpenEdgePort}：房子当前已"拆墙开口"的端面。"""
    return dict(getattr(house, "_edge_ports", {}) or {})


def house_closed_sides(house) -> list:
    """还没拆墙的侧别（有墙的那些边）。"""
    parts = getattr(house, "_parts", {}) or {}
    return [s for s, p in SIDE_TO_PART.items() if parts.get(p) is not None]


def house_ports(house) -> dict:
    """房子的完整端口视图：子部件具名端口（"bottom_wall:top_left"）+ 开口端面（端别名）。

    只是 `House.ports` 的**便捷取用**（同一个对象、同一份口径），保留本函数是为了
    让"从 opening 侧看房子有哪些口"这条老路径还能用。新的代码直接用 `house.ports`。

    注意：**只有拆过边（`open_house_side` / `HouseEdge.open`）的侧别才有端别名** ——
    端别名指向该边中点那一格的口。没拆边的侧别在这一层看不到任何东西，
    它的墙端口在 `<部件>:` 前缀下面（例如 `top_wall:top_left`）。
    """
    return dict(getattr(house, "ports", {}) or {})


def _detach_part(house, part_name: str) -> None:
    """把一个部件从组合结构里彻底摘掉：部件表 + 叶子映射 + under/over 列表。"""
    parts = getattr(house, "_parts", {}) or {}
    part = parts.get(part_name)
    if part is None:
        return
    parts[part_name] = None
    leaves = house._part_leaves(part)
    leaf_ids = {id(l) for l in leaves}
    house._under = [l for l in house._under if id(l) not in leaf_ids]
    house._over = [l for l in house._over if id(l) not in leaf_ids]
    leaf_part = getattr(house, "_leaf_part", {})
    for key in list(leaf_part.keys()):
        if key in leaf_ids:
            del leaf_part[key]
    house._all_leaves = house._under + house._over
    house._order_cache = None


def open_house_side(house, side: str, *, disconnect: bool = True):
    """拆掉房子某一侧的墙，把该侧变成可连接的开口（并在该侧装开口端面端口）。

    :param house: House 实例（或其子类，如 Hall）
    :param side: "north" / "south" / "west" / "east"
    :param disconnect: 是否同时断开与该墙相连的端口
                       （断开后剩下的侧墙会按"自由端"重新贴图，与走廊一致）
    :return: 被拆掉的部件（本来就没这面墙则 None）
    """
    side = str(side).lower()
    part_name = SIDE_TO_PART.get(side)
    if part_name is None:
        raise ValueError(f"side 必须是 {sorted(SIDE_TO_PART)} 之一，收到 {side!r}")
    parts = getattr(house, "_parts", {}) or {}
    part = parts.get(part_name)
    if part is None:
        return None

    if disconnect:
        ports = getattr(part, "ports", None)
        if isinstance(ports, dict):
            for port in list(ports.values()):
                try:
                    port.disconnect()
                except Exception:      # noqa: BLE001
                    pass

    _detach_part(house, part_name)

    edges = house_edge_ports(house)
    if side not in edges:
        edges.update(make_edge_ports(house, parts.get("floor"), (side,)))
        house._edge_ports = edges
    return part
