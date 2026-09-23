"""house_edge.py —— 房子【边缘接口】（HouseEdge）：一条边拆成若干格口，可接多条走廊。

## 要解决的问题

房子的一条边本来就是**一整面墙**（南北边是横墙、东西边是竖墙）。要在同一条边上
接【若干条走廊】，就必须把这条边拆开，让每一条走廊各自认领它正对的那几格：

    拆墙(open_house_side) → 这条边变成一排【单格接口】(HouseEdgePort)
      · 走廊开口认领它正对的那几格（走廊占的格不补墙）；
      · 剩下的格用【补墙小段】填上 —— 也就是把原来那面墙按剩下的格宽拆成几段小墙，
        缝隙就补平了，房子那条边看起来仍然是一道连续的墙。

## 为什么这么摆一定对（几何由端口吸附算，不手写 offset）

    走廊开口 ↔ 边缘格：OpenEdge ↔ OpenEdge
        side = 走廊端朝外的那条边 ↔ 边缘格朝外的边（top↔bottom / left↔right）
        exact 吸附 = 锚定格沿轴向偏【一格】→ 走廊地板与房子地板正好相邻一格。

    补墙小段 ↔ 边缘格：HorizontalWallBottomLeft ↔ OpenEdge（bottom ↔ bottom）
        side_a == side_b 时走 OverlapMixinPort 的【完全重叠】吸附 = 格心重合，
        与房子里原来那面底墙落到地板上的方式**完全一致**
        （RectangleHouse.connect_all ①就是底墙下角 ↔ 地板下角 bottom↔bottom）。

两侧都只是"拿端口 connect_to"，规则全部查全局规则表（OpenEdge/墙族四角已注册），
所以位置、朝向、贴合方式都由端口吸附决定 —— 换尺寸/换朝向不用改这里。

## 用法

    edge = HouseEdge.open(house, "south")      # 拆掉南墙，装成一排单格口
    edge.attach_hall(hall_a, at=0)             # 走廊 A 认领 0..1 格
    edge.attach_hall(hall_b, at=4)             # 走廊 B 认领 4..5 格
    edge.fill_gaps()                           # 剩下的格补小墙（没有剩余就不动）

    # 或者一步到位（自动排布 + 补墙）：
    report = connect_halls_to_house(house, "south", [hall_a, hall_b])

## 占格规则

  · 走廊占【地板格】(at .. at+span-1) + 两侧【墙格】(at-1 / at+span)：
    走廊的两道侧墙就立在这两个格上（在边的最外侧时正好落进房子的侧墙列，
    与房子自己的侧墙连成一堵墙）。
  · 一格只能被认领一次：走廊地板格被重复认领会直接报错（不会悄悄叠墙）；
    侧墙格允许与另一条走廊的侧墙格重合（两条走廊共用一堵墙，几何完全重合）。
  · 补墙只补【地板格】里还剩的连续区间；房子的侧墙列不在补墙范围内。
"""

from __future__ import annotations

from src.core.build_game.port import OverlapMixinPort, ConnectError
from src.core.build_game.opening import (OpenEdgePort, OPEN_EDGE_TYPE, OUTWARD,
                                         open_house_side, SIDE_TO_PART, _detach_part)
from src.core.build_game.horizontalwall import HorizontalWoodWall, HorizontalWall
from src.core.build_game.verticalwall import VerticalWoodWall, VerticalWall
from src.core.build_game.floor import BrickFloor, Floor
from src.core.build_game.hall import _ascii, _ensure_rules

# 边 -> 该边"朝外"的方向边（连接时边缘格用哪条边）
EDGE_OUTWARD = {"north": "top", "south": "bottom", "west": "left", "east": "right"}
# 边缘格端口只开放"朝外"那一条边：一格只能接一个东西（走廊开口 或 补墙小段）
EDGE_CELL_SIDES = {side: (out,) for side, out in EDGE_OUTWARD.items()}
# 边 -> 原来守在这条边上的标准部件名（拆的就是这面墙）
EDGE_WALL_PART = dict(SIDE_TO_PART)
NS_EDGES = ("north", "south")
EW_EDGES = ("west", "east")
# 边 -> 走廊该用哪个开口贴上来（南北边用走廊的南北口，东西边用东西口）
HALL_END_BY_EDGE = {"north": "south", "south": "north", "west": "east", "east": "west"}


def _part_art_top(part):
    """部件所有叶子贴图的最上沿 Y（世界坐标）；没有叶子时返回 None。

    「顶端对齐」量的必须是**贴图**顶边，不是网格几何顶边 —— 横墙的木纹贴图
    自带描边，视觉顶边和网格边界不是同一条线（见 horizontalwall / entity 里的
    House_40/41/42 瓦片）。用贴图顶边对齐，接缝处的描边才会连成一条线。
    """
    if part is None:
        return None
    ys = []
    for leaf in part.iter_sorted_leaf_sprites():
        wp = getattr(leaf, "world_position", None)
        if wp is not None:
            ys.append(float(wp.y))
    return min(ys) if ys else None


class HouseEdgePort(OverlapMixinPort, OpenEdgePort):
    """房子边缘【单格】接口：一条边的每一格各一个，最多接一个对端。

    类型沿用 `OpenEdge`（开口端面）：所以"走廊开口 ↔ 边缘格"走的就是
    (OpenEdge, OpenEdge) 这条已有规则；"补墙小段 ↔ 边缘格"走
    (OpenEdge, HorizontalWallBottomLeft) —— 两条规则都已注册。

    混入 OverlapMixinPort：同边（bottom↔bottom）连接时格心重合（补墙小段贴在
    地板边缘上），异边（top↔bottom）时按格宽偏一格（走廊地板与房子地板相邻）。
    也就是说"接走廊"和"补墙"用的是同一个口，只是连接方向不同。
    """

    port_type = OPEN_EDGE_TYPE
    max_connections = 1

    def __init__(self, structure, index, cell, *, side, host_part="floor"):
        OpenEdgePort.__init__(self, structure, index, cell,
                              sides=EDGE_CELL_SIDES[side], end=side,
                              host_part=host_part)

    def describe(self):
        return (f"HouseEdge[{self.end}#{self.index}] cell={self.cell} "
                f"sides={list(self.connectable_sides)} struct="
                f"{type(self.structure).__name__}")


class HouseEdge:
    """房子某一条边上的【单格接口表】+ 认领情况 + 补墙。

    一格里接什么由这里记账（`owner(index)`）：走廊地板格 / 走廊侧墙格 / 补墙段格。
    """

    def __init__(self, house, side, *, removed_wall=None, height=None, cell=None):
        side = str(side).lower()
        if side not in EDGE_OUTWARD:
            raise ValueError(f"side 必须是 {sorted(EDGE_OUTWARD)} 之一，收到 {side!r}")
        self.house = house
        self.side = side
        self.floor = house.part("floor")
        if self.floor is None:
            raise ValueError("房子没有地板部件（floor），无法装边缘接口")
        self.removed_wall = removed_wall
        self.wall_part_name = EDGE_WALL_PART.get(side)   # 原来守在这条边上的墙部件名
        self._height = int(height) if height else None
        self._cell = tuple(cell) if cell else None
        self.log: list[str] = []
        self.segments: list = []           # 补墙小段（已挂进房子 _parts）
        self.halls: list = []              # 这条边上认领过格子的走廊（按认领顺序）
        self.joints: list = []             # 墙-墙接头（wall_joint 建立的那些）
        self._ports: dict[int, HouseEdgePort] = {}
        self._owner: dict[int, str] = {}   # 格序号 -> 认领者描述

        gw = int(getattr(self.floor, "_grid_width", 0) or 0)
        gh = int(getattr(self.floor, "_grid_height", 0) or 0)
        if gw < 1 or gh < 1:
            raise ValueError("地板网格尺寸异常，无法装边缘接口")
        if side in NS_EDGES:
            row = 0 if side == "north" else gh - 1
            coords = [(c, row) for c in range(gw)]
        else:
            col = 0 if side == "west" else gw - 1
            coords = [(col, r) for r in range(gh)]
        self._cells = coords
        for i, cell in enumerate(coords):
            self._ports[i] = HouseEdgePort(house, i, cell, side=side)
        self._register_on_house()

    # ------------------------------------------------------------------ #
    # 建立
    # ------------------------------------------------------------------ #
    @classmethod
    def open(cls, house, side, *, disconnect: bool = True, height=None, cell=None,
             log=None) -> "HouseEdge":
        """拆掉这条边的墙，并把它装成一排单格接口（返回 HouseEdge）。

        :param disconnect: 是否同时断开被拆那面墙的端口（默认 True）——
                           断开后房子剩下的侧墙会按"自由端"重贴图（与拆墙语义一致）。

        幂等：同一条边重复 open 会复用第一次装好的接口表（不会把边拆第二遍、
        也不会丢掉已经接好的走廊）。
        """
        _ensure_rules()
        side = str(side).lower()
        existing = house_edge(house, side)
        if existing is not None:
            if log is not None:
                log.append(_ascii(f"[EDGE] house {side}: 这条边已经拆开过了，复用现成的接口表"))
            return existing
        removed = open_house_side(house, side, disconnect=disconnect)
        edge = cls(house, side, removed_wall=removed, height=height, cell=cell)
        if log is not None:
            log.append(_ascii(
                f"[EDGE] house {side}: 拆墙 {type(removed).__name__ if removed else '无'}，"
                f"装 {edge.length} 个单格口"))
        return edge

    def _register_on_house(self):
        """把本边缘登记到房子上（便于从房子反查：house_edge(house, side)）。"""
        edges = getattr(self.house, "_house_edges", None)
        if not isinstance(edges, dict):
            edges = {}
            try:
                self.house._house_edges = edges
            except Exception:      # noqa: BLE001
                return
        edges[self.side] = self

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    @property
    def length(self) -> int:
        """这条边上的格数（南北边 = 地板列数，东西边 = 地板行数）。"""
        return len(self._ports)

    @property
    def cells(self) -> list:
        return list(self._cells)

    def port(self, index: int):
        return self._ports.get(int(index))

    def ports(self) -> dict:
        return dict(self._ports)

    def owner(self, index: int):
        return self._owner.get(int(index))

    def claimed(self) -> set:
        return set(self._owner)

    def free_cells(self) -> list:
        return [i for i in range(self.length) if i not in self._owner]

    def free_runs(self) -> list:
        """还没被认领的连续格区间 [(start, stop), ...]（左闭右开）。"""
        runs, start = [], None
        for i in range(self.length):
            if i in self._owner:
                if start is not None:
                    runs.append((start, i))
                    start = None
            elif start is None:
                start = i
        if start is not None:
            runs.append((start, self.length))
        return runs

    def describe(self) -> str:
        return (f"HouseEdge(house={type(self.house).__name__}, side={self.side}, "
                f"拆掉的墙={self.wall_part_name}, "
                f"格数={self.length}, 已认领={len(self._owner)}, "
                f"补墙段={len(self.segments)}, 空闲={self.free_cells()})")

    def __repr__(self):
        return f"HouseEdge({self.side}, {len(self._owner)}/{self.length})"

    # ------------------------------------------------------------------ #
    # 接走廊
    # ------------------------------------------------------------------ #
    def attach_hall(self, hall, at: int = None, *, geometry: str = "exact",
                    share_walls: bool = True, log=None):
        """把一条走廊接到这条边上（走廊为主动方，会被平移贴上来）。

        :param hall: Hall 实例；其开口朝向必须与这条边匹配（南北边要 NS 走廊，
                     东西边要 EW 走廊）。
        :param at: 走廊地板占的**第一格**序号（None = 自动找第一个放得下的位置）。
        :param geometry: "exact"（默认：锚定格沿轴向偏一格 → 地板与房子地板相邻）。
        :param share_walls: 走廊侧墙格是否允许与另一条走廊的侧墙格重合（共用一堵墙）。
        :return: PortConnectResult（失败会抛 ConnectError）
        """
        _ensure_rules()
        end = HALL_END_BY_EDGE[self.side]
        edge_ends = hall.edge_ports() if hasattr(hall, "edge_ports") else {}
        if end not in edge_ends:
            raise ValueError(
                f"走廊开口端不匹配：{self.side} 边需要走廊的 {end!r} 开口，"
                f"而它只有 {sorted(edge_ends)}（NS 走廊接南北边，EW 走廊接东西边）")

        span = self._hall_span(hall)
        at = self._auto_at(span, share_walls=share_walls) if at is None else int(at)
        if at < 0 or at + span > self.length:
            raise ValueError(
                f"走廊（占 {span} 格）放不进这条边：at={at}，边格数={self.length}")

        # ---- 先检查：地板格逐格独占，侧墙格最多与另一条走廊的侧墙共用 ----
        for i in range(at, at + span):
            self._require_free(i, "走廊地板")
        for i in (at - 1, at + span):
            if 0 <= i < self.length:
                self._require_free(i, "走廊侧墙", share=share_walls)

        # ---- 连：查到哪一格的口，就由端口吸附把走廊摆到位 ----
        anchor = at + span // 2
        peer = self._ports[anchor]
        res = hall.port(end).connect_to(
            peer, self_side=OUTWARD[end], peer_side=EDGE_OUTWARD[self.side],
            geometry=geometry)

        # ---- 不重叠 + 向外靠一格 + 走廊自己补一排 floor ----
        # 只有【东西边】需要这一步。东西边接的是 EW 走廊，它的两道侧墙是横墙，
        # 按 overlap_bottom 摆放（与房子前墙同款，压在走廊地板最后一行上）——
        # 紧贴接缝的那一格地板被墙压住，看起来"地板少了一格纹理"。
        # 做法：整条走廊连同它的墙再往外挪一格，再由走廊自己补一排 floor 把
        # 挪开后空出来的那一格填上。净占地一格不变（接缝那格仍有地板），
        # 墙却不再压住它；挪过一格之后，竖墙段与走廊墙的关系变成"跨半格"，
        # 走 VerticalWall 的 head_cross / tail_cross 那一档。
        #
        # 南北边【不能】这么做：那里接的是 NS 走廊，侧墙是垂直于边的竖墙、
        # 立在走廊两侧的相邻列上，本来就不压地板；挪一格反而会让走廊竖墙的头
        # 从补墙段顶格上挪开，接头直接找不到（实测 peers 变空）。
        if self.side not in NS_EDGES:
            self._push_hall_outward(hall)
            self._add_hall_edge_floor(hall)

        tag = self._hall_tag(hall)
        for i in range(at, at + span):
            self._owner[i] = f"hall:{tag}"
        # 记下"走廊在这条边上占了哪几行"：补墙段要跟走廊的横墙落在同一行才能接头
        # （见 _slice_of_removed_wall 里往回多要一行的说明）。
        if not hasattr(self, "_hall_rows"):
            self._hall_rows = []
        self._hall_rows.append((int(at), int(span)))
        # 侧墙格只在走廊的墙**真的盖住**它时才认领（见 _hall_wall_cells）：
        # 认领了却不补墙，那一格就是个永远的洞（表现为"竖墙太短/断开"）。
        claimed_walls = self._hall_wall_cells(hall, at, span)
        for i in claimed_walls:
            self._owner[i] = "hall_wall"
        line = (f"[OK] {self.side} 边认领 {at}..{at + span - 1} <- {tag}"
                f"（侧墙格 {at - 1}/{at + span}，实测覆盖 {claimed_walls}） "
                f"{res.summary()}")
        self.log.append(_ascii(line))
        if log is not None:
            log.append(_ascii(line))
        if hall not in self.halls:
            self.halls.append(hall)
        # 墙与墙：把这条边上相接的墙端按既有规则连起来（贴图随之替换）
        self.join_walls(log=log)
        return res

    # ------------------------------------------------------------------ #
    # 走廊侧墙到底盖住了哪几格（认领前实测，而不是无条件 at-1/at+span）
    # ------------------------------------------------------------------ #
    def _outward_step(self):
        """"向外"（远离房子）一格的世界向量 + 用的格尺寸。"""
        floor = self.floor
        cw = float(getattr(floor, "_cell_w", 0) or 64)
        ch = float(getattr(floor, "_cell_h", 0) or 64)
        if self.side == "north":
            return (0.0, -ch), cw, ch
        if self.side == "south":
            return (0.0, ch), cw, ch
        if self.side == "west":
            return (-cw, 0.0), cw, ch
        return (cw, 0.0), cw, ch

    def _push_hall_outward(self, hall) -> None:
        """把走廊整体（含它的墙）再往外挪一格（见 attach_hall 的说明）。"""
        (dx, dy), _cw, _ch = self._outward_step()
        mover = getattr(hall, "move_by", None)
        if callable(mover):
            mover(dx, dy)

    def _add_hall_edge_floor(self, hall, *, name: str = "edge_floor") -> None:
        """给走廊补一排 floor，填上"向外挪一格"后空出来的那一格。

        形状：与走廊轴向【垂直】的一排 —— 南北边接的是 NS 走廊（地板 gw×gh），
        往外走沿 Y，空出的是一【行】→ (gw, 1)；东西边接 EW 走廊，往外走沿 X，
        空出的是一【列】→ (1, gh)。材质跟走廊自己的地板保持一致。
        """
        parts = getattr(hall, "_parts", {}) or {}
        if parts.get(name) is not None:
            return                      # 幂等：同一条走廊只补一次
        floor = hall.part("floor") if hasattr(hall, "part") else None
        if floor is None:
            return
        gw = int(getattr(floor, "_grid_width", 0) or 0)
        gh = int(getattr(floor, "_grid_height", 0) or 0)
        if gw < 1 or gh < 1:
            return
        (dx, dy), cw, ch = self._outward_step()
        cx, cy = float(floor.shape.centerx), float(floor.shape.centery)
        # 补的那一排落在"新地板靠房子那一侧的外面一格"：
        # 沿轴上要走出半个新地板 + 半个自己，即 (格数+1)/2 格 ——
        # 写成"中心往回一格"会差 (格数-1)/2 格，补的排会压到走廊自己地板上，
        # 空出来的那格反而没人填（画面上就是一个露出背景色的洞）。
        if self.side in NS_EDGES:
            size = (gw, 1)
            cx2 = cx
            back = (gh + 1) * ch / 2.0
            cy2 = cy - back if self.side == "south" else cy + back
        else:
            size = (1, gh)
            cy2 = cy
            back = (gw + 1) * cw / 2.0
            cx2 = cx - back if self.side == "east" else cx + back
        wood = bool((getattr(hall, "params", {}) or {}).get("use_wood", True))
        try:
            if wood:
                strip = BrickFloor(size, (cx2, cy2))
            else:
                strip = Floor(size, (float(cw), float(ch)), (cx2, cy2))
        except Exception:      # noqa: BLE001
            return
        try:
            hall.add_part(name, strip, layer="under")
        except Exception:      # noqa: BLE001
            pass

    def _edge_cell_rect(self, index: int):
        """边格 index 的世界矩形（取房子地板在该格上的精灵）。"""
        floor = self.floor
        gw = int(getattr(floor, "_grid_width", 0) or 0)
        gh = int(getattr(floor, "_grid_height", 0) or 0)
        if gw < 1 or gh < 1:
            return None
        if self.side in NS_EDGES:
            row = 0 if self.side == "north" else gh - 1
            cell = (int(index), row)
        else:
            col = 0 if self.side == "west" else gw - 1
            cell = (col, int(index))
        getter = getattr(floor, "get_sprite_at_grid", None)
        sprite = getter(cell) if callable(getter) else None
        return getattr(sprite, "world_position", None) if sprite is not None else None

    def _hall_wall_cells(self, hall, at: int, span: int) -> list:
        """走廊的两道侧墙**实际盖住**了这条边的哪几格。

        为什么不直接认 `at-1` / `at+span`：那两格只是"几何上应该"被走廊侧墙占住。
        NS 走廊的侧墙是**垂直于这条边**的竖墙，确实落在 at-1 与 at+span 上；
        但 EW 走廊的侧墙是横墙，按 overlap_bottom 的摆法是**压在走廊地板最后一行上**，
        并不盖 at+span —— 无条件认领就等于宣告"这格有人管"，`fill_gaps` 于是不补，
        那条边永远缺一格（看起来"竖墙太短、断开"）。

        判据：边格中心落在走廊某片墙叶子的矩形里，就算被盖住。
        """
        want = [i for i in (at - 1, at + span) if 0 <= i < self.length]
        if not want:
            return []
        try:
            from src.core.build_game.wall_joint import hall_side_walls
            leaves = []
            for wall in hall_side_walls(hall):
                for leaf in wall.iter_sorted_leaf_sprites():
                    rect = getattr(leaf, "world_position", None)
                    if rect is not None:
                        leaves.append(rect)
        except Exception:      # noqa: BLE001
            return want          # 拿不到叶子就退回旧行为（认 at-1/at+span）
        if not leaves:
            return want
        out = []
        for i in want:
            rect = self._edge_cell_rect(i)
            if rect is None:
                out.append(i)
                continue
            cx, cy = rect.centerx, rect.centery
            for lr in leaves:
                try:
                    if lr.left <= cx <= lr.right and lr.top <= cy <= lr.bottom:
                        out.append(i)
                        break
                except Exception:      # noqa: BLE001
                    out.append(i)
                    break
        return out

    def join_walls(self, *, connect: bool = True, corners: bool = True, log=None):
        """把这条边附近相接的墙端接起来（房子侧墙 / 走廊侧墙 / 补墙小段）。

        贴图替换完全走既有逻辑：共线续接 → 贯通纹；成角相接 → 转角纹。
        返回新建立的接头列表（也累计在 self.joints 上）。
        """
        from src.core.build_game.wall_joint import join_house_edge
        out_log = [] if log is None else log
        joints = join_house_edge(self, connect=connect, corners=corners, log=out_log)
        for j in joints:
            self.joints.append(j)
            if log is not None:
                log.append(_ascii(f"[JOINT] {self.side} 边 {j['kind']} "
                                  f"{type(j['a']).__name__}.{j['name_a']} <-> "
                                  f"{type(j['b']).__name__}.{j['name_b']}"))
        return joints

    def _hall_span(self, hall) -> int:
        """走廊在这条边上占几格（南北边 = 走廊地板列数，东西边 = 走廊地板行数）。"""
        floor = hall.part("floor") if hasattr(hall, "part") else None
        if floor is None:
            raise ValueError(f"{type(hall).__name__} 没有地板，无法接房子")
        gw = int(getattr(floor, "_grid_width", 0) or 0)
        gh = int(getattr(floor, "_grid_height", 0) or 0)
        return gw if self.side in NS_EDGES else gh

    @staticmethod
    def _hall_tag(hall) -> str:
        p = getattr(hall, "params", {}) or {}
        return (f"hall({p.get('orientation', '?')}"
                f"{p.get('length', '?')}x{p.get('width', '?')})")

    def _auto_at(self, span: int, *, share_walls: bool = True) -> int:
        """自动找第一个放得下的位置（地板格必须全空；侧墙格允许落进房子侧墙列）。"""
        for at in range(0, self.length - span + 1):
            if any(i in self._owner for i in range(at, at + span)):
                continue
            ok = True
            for i in (at - 1, at + span):
                if 0 <= i < self.length and i in self._owner and not share_walls:
                    ok = False
                    break
            if ok:
                return at
        raise ValueError(f"这条边（{self.length} 格）放不下再一条占 {span} 格的走廊了")

    def _require_free(self, index: int, who: str, *, share: bool = False):
        """一格只能被认领一次；share=True 时允许与"走廊侧墙"共用。"""
        index = int(index)
        if index < 0 or index >= self.length:
            return                      # 边两端的房子侧墙列：不算边格，不用记账
        owner = self._owner.get(index)
        if owner is None:
            return
        if share and owner == "hall_wall":
            return
        raise ValueError(f"边格 {index} 已被 {owner} 占用，{who} 不能再用它")

    # ------------------------------------------------------------------ #
    # 补墙：把没被认领的连续格用小墙段填上
    # ------------------------------------------------------------------ #
    def fill_gaps(self, *, log=None) -> list:
        """把没被走廊认领的连续格拆成【小墙段】补上，并挂进房子（House.add_part）。

        :return: 新建的墙段列表（顺序 = 从边的一端到另一端）
        """
        _ensure_rules()
        made = []
        for start, stop in self.free_runs():
            seg = self._build_segment(start, stop - start)
            if seg is None:
                continue
            made.append(seg)
            for i in range(start, stop):
                self._owner[i] = "wall"
        if made:
            widths = []
            for s in made:
                widths.append(int(s._grid_width) if self.side in NS_EDGES
                              else int(getattr(s, "_v_length", 0) or 0))
            line = f"[OK] {self.side} 边补墙 {len(made)} 段（各段长 {widths}）"
            self.log.append(_ascii(line))
            if log is not None:
                log.append(_ascii(line))
            # 补墙段与两旁的走廊侧墙 / 房子侧墙现在成了"端面对端面"，
            # 按既有规则补上接头（贴图随之替换成转角纹 / 贯通纹）
            self.join_walls(log=log)
        return made

    def _wall_height(self) -> int:
        """这条边的墙有几行高（优先用拆掉的那面墙反推，最稳）。"""
        if self._height:
            return self._height
        w = self.removed_wall
        gh = int(getattr(w, "_grid_height", 0) or 0)
        floor_h = int(getattr(self.floor, "_grid_height", 0) or 0)
        if self.side in NS_EDGES and gh >= 2:
            return gh                                  # 横墙：网格行数就是墙高
        if self.side in EW_EDGES and gh >= 2:
            h = gh - floor_h                           # 竖墙：行数 = (宽+1) + 高 - 1
            if h >= 2:
                return h
        # 墙已经被拆掉了（或没拆过）：问房子里还在的墙高（属性就在部件上）
        for name in ("left_wall", "right_wall"):
            v = int(getattr(self.house.part(name), "_v_height", 0) or 0)
            if v >= 2:
                return v
        for name in ("top_wall", "bottom_wall"):
            g = int(getattr(self.house.part(name), "_grid_height", 0) or 0)
            if g >= 2:
                return g
        raise ValueError("推不出墙高，请给 HouseEdge(height=...) 或先 open 拆墙")

    def _material(self):
        """补墙用什么材料/格尺寸：跟被拆掉的那面墙保持一致。"""
        if self._cell:
            cell = self._cell
        else:
            w = self.removed_wall
            cell = (float(getattr(w, "_cell_w", 64) or 64),
                    float(getattr(w, "_cell_h", 64) or 64))
        name = type(self.removed_wall).__name__ if self.removed_wall is not None else ""
        if self.removed_wall is None:
            wood = True
        else:
            wood = ("Wood" in name)
        return wood, cell

    def _build_segment(self, start: int, width: int):
        """造一段补墙：横墙按剩余格数造短墙，竖墙按原墙克隆后【挖格】成一段。"""
        height = self._wall_height()
        wood, cell = self._material()
        if self.side in NS_EDGES:
            # 南北边：横墙小段。横墙的贴图逐行固定（40/41/42），"按剩余格数造一堵
            # 短墙"逐格就等于原墙的那一段 —— 宽度 1 时引擎需要显式允许条状形状。
            if wood:
                seg = HorizontalWoodWall((width, height), (0, 0),
                                         overlap_bottom=True, allow_1xN=True)
            else:
                seg = HorizontalWall((width, height), cell, (0, 0),
                                     overlap_bottom=True, allow_1xN=True)
            # 宽 1 时四角名塌缩到同一格，端口按【角角色】合并成"左右都能连"的
            # 退化角口（见 horizontalwall.corner_roles_of）—— bottom_left 一定在。
            port = seg.port("bottom_left")
            anchor, a_side = start, "bottom"
            seg = self._place_segment(seg, port, anchor, a_side, start, width)
        else:
            # 东西边：竖墙。竖墙的贴图跟"这堵墙有多长"直接相关（头/中段/尾/正立面
            # 各自落在第几行），按剩余格数造短墙会把头尾挤到中间 —— 表现出来就是
            # "在横墙的上面和下面断开了"。所以这里按【原来那面墙】克隆一堵，
            # 再把它范围之外的格挖掉：每一段逐格等于原墙在该行的那一格，
            # 头配横墙的头、尾配横墙的尾，位置全都回到原来的行上。
            seg = self._slice_of_removed_wall(start, width)
        if seg is None:
            return None
        name = f"edge_wall_{self.side}_{start}"
        layer = "over" if self.side == "south" else "under"
        self.house.add_part(name, seg, layer=layer)
        self.segments.append(seg)
        self.log.append(_ascii(
            f"[OK] {self.side} 边补墙 {start}..{start + width - 1}"
            f"（{type(seg).__name__} 网格={seg._grid_width}x{seg._grid_height}"
            f"{'，挖掉 ' + str(len(seg.masked_cells())) + ' 格' if seg.masked_cells() else ''}）"))
        return seg

    def _place_segment(self, seg, port, anchor: int, a_side: str, start: int, width: int):
        """把墙段用【端口吸附】摆到边缘格上（横墙段走这条）。"""
        try:
            port.connect_to(self._ports[anchor], self_side=a_side,
                            peer_side=EDGE_OUTWARD[self.side], geometry="exact")
        except ConnectError as e:
            self.log.append(_ascii(f"[REJECT] {self.side} 边补墙 {start}+{width} "
                                   f"{type(e).__name__}: {e}"))
            return None
        return seg

    def _slice_of_removed_wall(self, start: int, width: int):
        """在 edge 的空隙里【新建一道独立的小竖墙】—— 不克隆原墙、不挖格。

        为什么不能"把原墙切开画"：克隆 + 挖格只是把一整面墙的格子掐掉几行，
        段上剩下的行**仍然是原墙那一行的段语义**（middle / bottom / 侧立面…），
        也就是这堵"小墙"没有自己的头、没有自己的尾、没有自己的墙脚 —— 它只是
        一块被切下来的墙皮，透视上当然对不上。

        这里改成：这一段自己就是一堵完整的竖墙。
          · 网格行数 = 这一段占的地面行数（所以"长度"就是段的长度，不额外增长）；
          · 竖墙网格 = L + H - 1 行 → L = width - H + 1（至少 1），
            H 取房子/走廊的墙高 —— 于是它的头/中段/尾/墙脚都落在**自己**身上，
            并且和走廊的墙**same height**，透视一致；
          · 段最南那一行 = 竖墙的底格(gh-1)，`tail` 到 `bottom` 正好相隔 H-1 行
            （与普通竖墙的端口关系完全一样，连接逻辑不用特判）；
          · 摆位直接算：网格行 k ↔ 地面行 (start + k)，不靠端口吸附。
        """
        base = self.removed_wall
        height = int(getattr(base, "_v_height", 0) or 0) if base is not None else 0
        if height < 2:
            height = self._wall_height()
        wood, cell = self._material()

        # 紧贴走廊【南侧】的那一段往回多占一行：走廊的横墙是 overlap_bottom 摆法，
        # 底行压在它地板最后一行上（at+span-1）。段要跟它落在**同一行**（列相邻）
        # 才能形成 corner 接头；差一行就是斜对角 —— find_joint 的 side_by 要 dy≈0、
        # above_below 要 dx≈0，斜对角两不沾，接头直接被跳过（"下半的连接没有了"）。
        if any(start == at + span for at, span in getattr(self, "_hall_rows", ())):
            start -= 1
            width += 1

        # 网格行数 = 这一段占的地面行数
        length = max(1, int(width) - int(height) + 1)
        if wood:
            seg = VerticalWoodWall(length, height)
        else:
            seg = VerticalWall(length, height, cell)
        gh = int(seg._grid_height)

        # 摆位：网格行 k ↔ 地面行 (start + k)，所以第 0 行的地面行号是
        #   top_ground = start + width - gh
        # （width < H 时 gh > width，段会自然向北上探 H-width 行 —— 这是墙"立起来"
        #   的部分，属于墙自身的结构，不是额外增长。）
        f_top = float(self.floor._get_top_left()[1])
        ch = float(getattr(seg, "_cell_h", 0) or 64)
        top_ground = int(start) + int(width) - gh
        cx = float(base.shape.centerx) if base is not None else \
            float(self.floor.shape.centerx)
        cy = f_top + (top_ground + gh / 2.0) * ch
        try:
            seg.set_center((cx, cy))
        except Exception:      # noqa: BLE001
            pass
        return seg

    # ------------------------------------------------------------------ #
    # 按走廊【实际位置】重新封住 edge
    # ------------------------------------------------------------------ #
    # 背景：房子一条边上的墙被拆掉之后，那条边就是一排单格口；走廊贴上来之后，走廊的
    # 两道墙是**垂直于这条边**的两条边，只盖住其中几格，剩下的格由房子补小墙
    # （fill_gaps）——"原来一面墙变成好几道独立的墙"。
    #
    # fill_gaps 是拿**格号**算的（走廊占第 at..at+span-1 格，补墙补剩下的格），
    # 只有"走廊正好整格落在那几行"时才严丝合缝。如果调用方把走廊（或它的某一道墙）
    # 挪过，格号就和实际落位对不上，那条边上会留下没封住的洞。
    # reseal_after_hall() 就是按**走廊墙的实际世界位置**重算一遍补墙段。
    #
    # 注意：它保证的是"不留洞 / 不叠墙"，不保证和 fill_gaps 切在完全相同的接缝行上
    # （接缝落在哪一行属于观感问题，随走廊落位而定）。
    def clear_segments(self, *, log=None) -> int:
        """把本边已经补好的小墙段全部摘掉（为"按走廊新位置重封"让路）。

        :return: 摘掉的段数
        """
        removed = 0
        for seg in list(self.segments):
            for name, part in list((getattr(self.house, "_parts", {}) or {}).items()):
                if part is not seg:
                    continue
                _detach_part(self.house, name)
                extra = getattr(self.house, "_extra_parts", None)
                if isinstance(extra, list):
                    self.house._extra_parts = [t for t in extra if t[0] != name]
                removed += 1
        self.segments = []
        self._owner = {k: v for k, v in self._owner.items() if v != "wall"}
        if removed and log is not None:
            log.append(_ascii(f"[EDGE] {self.side} 边摘掉 {removed} 段补墙（准备按走廊位置重封）"))
        return removed

    def reseal_after_hall(self, hall, *, log=None) -> list:
        """按走廊**实际落位**重封这条边：摘掉旧补墙，按走廊墙盖住的行重切几段补上。

        走廊整体挪过、或只挪了它的一道墙之后调这个，edge 上不会留下没封住的洞。

        :return: 新补的墙段列表（顺序 = 从边的一端到另一端）
        """
        _ensure_rules()
        removed = self.clear_segments(log=log)
        made = []
        for start, stop in self._gaps_around_hall(hall):
            seg = self._seal_segment(start, stop - start)
            if seg is not None:
                made.append(seg)
                for i in range(max(0, start), min(stop, self.length)):
                    self._owner[i] = "wall"
        if log is not None:
            log.append(_ascii(
                f"[OK] {self.side} 边按走廊实际位置重封：摘 {removed} 段、补 {len(made)} 段"))
        if made:
            self.join_walls(log=log)
        return made

    def _gaps_around_hall(self, hall) -> list:
        """这条边上"走廊的两道墙都没盖住"的行区间 [(start, stop), ...]。

        两道墙的外沿并集算作"被走廊盖住"：走廊内部两道墙之间那一行是门洞，
        本来就由走廊自己露着，不算房子的缺口。
        """
        cell = float(getattr(self.floor, "_cell_h", 0) or 64)
        fy = float(self.floor.world_position.y)
        top = bottom = None
        for name in ("top_wall", "bottom_wall", "left_wall", "right_wall"):
            part = hall.part(name) if hasattr(hall, "part") else None
            if part is None:
                continue
            for leaf in part.iter_sorted_leaf_sprites():
                wp = leaf.world_position
                top = float(wp.y) if top is None else min(top, float(wp.y))
                bot = float(wp.y) + float(wp.height)
                bottom = bot if bottom is None else max(bottom, bot)
        if top is None:
            return [(0, self.length)] if self.length > 0 else []
        r0 = max(0, int(round((top - fy) / cell)))
        r1 = min(self.length, int(round((bottom - fy) / cell)))
        out = []
        if r0 > 0:
            out.append((0, r0))
        if r1 < self.length:
            out.append((r1, self.length))
        return out

    def _seal_segment(self, start: int, width: int):
        """按【地面行 start..start+width-1】造一段补墙。"""
        if width <= 0:
            return None
        if self.side in NS_EDGES:
            return self._build_segment(start, width)
        base = self.removed_wall
        if base is None:
            return None
        seg = self._slice_of_wall_rows(base, start, width)
        if seg is None:
            return None
        name = f"edge_wall_{self.side}_{start}"
        layer = "over" if self.side == "south" else "under"
        self.house.add_part(name, seg, layer=layer)
        self.segments.append(seg)
        self.log.append(_ascii(
            f"[OK] {self.side} 边重封 {start}..{start + width - 1}"
            f"（{type(seg).__name__} 网格={seg._grid_width}x{seg._grid_height}）"))
        return seg

    def _slice_of_wall_rows(self, base, start: int, width: int):
        """把某面竖墙克隆出来、只保留【地面行 start..start+width-1】那几格。

        和 fill_gaps 同一套做法（克隆摆回原墙中心、按行挖格），所以同一段切出来的
        贴图逐行一致；区别只是这里的行范围由调用方按"走廊墙的实际位置"算出来，
        不再等于格号。

        行号口径：克隆摆回原墙中心后，**每格的世界 Y 就是它站的那条地面线**
        （(y - 地板北缘) / 格高）。就按这个值筛格，不去反推网格行 ——
        竖墙网格里混着"墙顶立面行 / 地板行"两套语义，反推极易错一格。
        """
        height = int(getattr(base, "_v_height", 0) or self._wall_height())
        vlen = int(getattr(base, "_v_length", 0) or 0)
        wood, cell = self._material()
        if wood:
            seg = VerticalWoodWall(vlen, height)
        else:
            seg = VerticalWall(vlen, height, cell)
        try:
            seg.set_center((base.shape.centerx, base.shape.centery))
        except Exception:      # noqa: BLE001
            pass

        ch = float(getattr(seg, "_cell_h", 0) or 64)
        fy = float(self.floor.world_position.y)
        keep = set(range(start, start + width))
        drop = []
        for cell_pos, sprite in seg.shape.get_space():
            wp = getattr(sprite, "world_position", None)
            if wp is None:
                continue
            mark = int(round((float(wp.y) - fy) / ch))
            if mark not in keep:
                drop.append(cell_pos)
        if drop:
            seg.mask_cells(drop)
            _reset_slice_ports(seg)
        return seg


# ======================================================================
# 便捷入口
# ======================================================================
def _reset_slice_ports(seg) -> None:
    """挖完格之后重建切段的端口缓存。

    竖墙的端口是"这一格在墙的什么位置"派生出来的：挖格改变了可见范围，
    于是**段的两个端点格**可能从"无口"变成"有口"（见 VerticalWall.is_slice）。
    端点格本身没被挖掉（mask_cells 只清了被挖的格），所以必须显式重来一遍 ——
    否则懒生成的 None 会被缓存下来，段的两头永远接不上。
    """
    cache = getattr(seg, "_cell_ports_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def house_edge(house, side):
    """取房子某条边上的 HouseEdge（没开过这条边则 None）。"""
    edges = getattr(house, "_house_edges", None)
    return edges.get(str(side).lower()) if isinstance(edges, dict) else None


def connect_halls_to_house(house, side, halls, *, fill_gaps: bool = True,
                           geometry: str = "exact", share_walls: bool = True,
                           log=None):
    """把若干条走廊接到房子的一条边上，并把剩下的缝隙补上（一步到位）。

    :param halls: [hall, ...]（自动从边的一端依次排布）；也接受 [(hall, at), ...]
                  显式指定每条走廊占的第一格。
    :param fill_gaps: 是否把没被走廊占掉的格用小墙补上（默认 True）
    :return: {"edge": HouseEdge, "halls": [...], "segments": [...], "log": [...]}
    """
    out_log = log if log is not None else []
    edge = HouseEdge.open(house, side, log=out_log)
    placed = []
    for item in halls:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            hall, at = item
        else:
            hall, at = item, None
        edge.attach_hall(hall, at=at, geometry=geometry,
                         share_walls=share_walls, log=out_log)
        placed.append(hall)
    segs = edge.fill_gaps(log=out_log) if fill_gaps else []
    return {"edge": edge, "halls": placed, "segments": segs, "log": out_log}
