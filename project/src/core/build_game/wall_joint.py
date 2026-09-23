"""wall_joint.py —— 墙与墙的【接头】：把相接的两堵墙按既有连接规则连起来。

## 为什么需要它

房子拆边接走廊之后，场上多出了**新的墙端相接**：

    · 走廊的侧墙  ↔  房子的侧墙      （同一列，共线续接）
    · 走廊的侧墙  ↔  补墙小段        （错开一格，成角相接）
    · 走廊的侧墙  ↔  下一条走廊的侧墙（同一列，共线续接）

它们此前只是"被摆到了一起"，端口上毫无关系 —— 于是竖墙那套
「段 + 连接态 → 贴图」的老逻辑根本不会触发：

    · 墙头明明上面还接着一堵墙，却仍然画**自由端收口**（33）；
    · 成角那一边也不会出**转角纹**（8/10、24/26、跨半格的 22/15 …）。

本模块就是把这些相接的墙端**按老规矩补上连接** —— 用的词汇与方向和
`RectangleHouse.connect_all` 里那几句一模一样
（`vl.port("tail") ↔ dt.port("top_left")` 之类），连上之后
`VerticalWall.on_topology_settled()` 会照旧自己重算贴图。

## 两种接头（几何决定，不靠调用方声明）

    inline（共线续接）: 两根同列（或同行）的墙，端面格心**重合** → 本是一堵墙。
        端口照连（拓扑/存档/端口标记都拿得到），但贴图取**贯通纹**（16/18）——
        因为"尾段被连上"在竖墙表里是转角语义(24/15)，共线时用它就错了。

    corner（成角相接）: 一竖一横两堵墙，端面格心沿一条轴**相邻一格** → 转角。
        直接走既有逻辑：竖墙头 8/10（跨半格时 22）、尾 24/26/15/7 …

## 安全边界

接头**只在两堵墙本来就摆对位置时**建立：先按 port._snap_self_to_peer 的同一套算法
算出"吸附会挪多少"，不为 0（几何不自洽）就直接 [REJECT] 不连 —— 否则一端锁定后
会把主动方连同它已相连的结构一起挪走。连完之后还会比一次位置，万一动了会记 [WARN]。

接头是**当前拓扑的函数**：走廊一断开，`prune_joints` 会把失效的接头拆掉
（端口断开 + 抹掉共线笔记），墙头随之回到自由端纹。
"""

from __future__ import annotations

from src.core.build_game.port import RULES, ConnectError

INLINE = "inline"
CORNER = "corner"
SIDE_BY = "side_by"          # 平行两墙的端面互相顶着（竖墙头顶到另一堵竖墙上）

# 只有"端面口"参与墙-墙接头
VWALL_END_PORTS = ("top", "tail", "bottom")
HWALL_END_PORTS = ("top_left", "top_right", "bottom_left", "bottom_right")
_SIDE_OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


# ------------------------------------------------------------------ #
# 小工具
# ------------------------------------------------------------------ #
def wall_kind(wall):
    """"v"（竖墙）/ "h"（横墙）/ None。"""
    st = getattr(wall, "structure_type", None)
    if st == "vertical_wall":
        return "v"
    if st == "horizontal_wall":
        return "h"
    return None


def wall_end_ports(wall) -> list:
    """[(端口名, 端口)]：只取端面口（竖墙 top/tail/bottom，横墙四角）。"""
    kind = wall_kind(wall)
    if kind is None:
        return []
    ports = getattr(wall, "ports", None)
    if not isinstance(ports, dict):
        return []
    names = VWALL_END_PORTS if kind == "v" else HWALL_END_PORTS
    return [(n, ports[n]) for n in names if ports.get(n) is not None]


def _center(port):
    try:
        return port.cell_center_world()
    except Exception:      # noqa: BLE001
        return None


def _pos(wall):
    """墙的位置快照（接头前后比一比，确保没有被挪动）。"""
    try:
        return (round(wall.shape.centerx, 3), round(wall.shape.centery, 3))
    except Exception:      # noqa: BLE001
        return None


def _tag(wall) -> str:
    return f"{type(wall).__name__}@{id(wall) % 1000}"


def _log(log, text):
    if log is not None:
        log.append(text)


# ------------------------------------------------------------------ #
# 找接头
# ------------------------------------------------------------------ #
def find_joints(a, b, *, cell: float = 64.0, tol: float = 0.75) -> list:
    """两堵墙之间【全部】"端面正对"的接头（可能不止一条）。

    为什么必须返回列表：同一对墙可以有多处合法的端面相对。1 格宽的竖墙段就是
    典型 —— 上端贴着房子下墙的一个角、下端贴着同一堵墙的另一个角，两条都成立。
    原来只返回第一条，第二个角就永远轮不到（表现："另一半的连接没有了"）。

    每项 {kind, name_a, port_a, side_a, name_b, port_b, side_b}
      · INLINE ：端面格心重合，且两墙**同类同轴**（竖-竖 同列 / 横-横 同行）→ 贯通
      · CORNER ：端面格心沿一条轴相邻一格，且两墙**一竖一横**（成角）→ 转角
      · SIDE_BY：端面格心沿一条轴相邻一格，两墙**平行**（竖-竖 或 横-横）→
                 一堵墙的端头顶在另一堵墙身上（例：走廊侧墙顶到房子侧墙上）。
                 只认竖墙的头/尾口："底口"是连地板的口，不参与墙-墙接头。

    刻意**只在端面口**上找接头：两堵平行墙并排铺开（加点厚度）那不属于端面对端面，
    也永远不会因为"贴在一起"就自动连上 —— 那是并排，不是接头。
    """
    ka, kb = wall_kind(a), wall_kind(b)
    if ka is None or kb is None or a is b:
        return []

    out = []
    seen = set()
    for name_a, pa in wall_end_ports(a):
        ca = _center(pa)
        if ca is None:
            continue
        for name_b, pb in wall_end_ports(b):
            cb = _center(pb)
            if cb is None:
                continue
            dx, dy = cb[0] - ca[0], cb[1] - ca[1]
            same_spot = abs(dx) <= tol and abs(dy) <= tol
            side_by = abs(dy) <= tol and abs(abs(dx) - cell) <= tol
            above_below = abs(dx) <= tol and abs(abs(dy) - cell) <= tol

            if same_spot:
                if ka != kb:
                    continue                       # 共线续接只在同类墙之间成立
                # 共线：方向取"两墙各朝外那侧"——竖墙左右都行但要相反；
                # 尾段的 side 决定 _tail_cross（影响正面纹），所以让它取自己的朝向。
                if name_a == "tail":
                    side_a = getattr(a, "orientation", "right")
                    side_b = _SIDE_OPPOSITE.get(side_a, "left")
                elif name_b == "tail":
                    side_b = getattr(b, "orientation", "right")
                    side_a = _SIDE_OPPOSITE.get(side_b, "left")
                else:
                    side_a, side_b = "right", "left"
                kind = INLINE
            elif side_by or above_below:
                if ka == kb:
                    # 平行两墙的端面互相顶着：竖墙的头/尾顶到另一堵竖墙上
                    if ka != "v" or "bottom" in (name_a, name_b):
                        continue
                    kind = SIDE_BY
                else:
                    kind = CORNER
                if side_by:
                    left_is_a = dx > 0
                    side_a = "right" if left_is_a else "left"
                    side_b = _SIDE_OPPOSITE[side_a]
                else:
                    below_is_a = dy > 0
                    side_a = "bottom" if below_is_a else "top"
                    side_b = _SIDE_OPPOSITE[side_a]
            else:
                continue

            # 方向必须两侧都允许，且规则表里真有这条规则
            if side_a not in pa.connectable_sides or side_b not in pb.connectable_sides:
                continue
            if RULES.get(pa.edge_type(side_a), pb.edge_type(side_b)) is None:
                continue
            key = (id(pa), side_a, id(pb), side_b)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "name_a": name_a, "port_a": pa, "side_a": side_a,
                        "name_b": name_b, "port_b": pb, "side_b": side_b})
    return out


def find_joint(a, b, *, cell: float = 64.0, tol: float = 0.75):
    """兼容入口：只取第一条接头（旧调用点用）。"""
    joints = find_joints(a, b, cell=cell, tol=tol)
    return joints[0] if joints else None


# ------------------------------------------------------------------ #
# 建立接头
# ------------------------------------------------------------------ #
def _note_inline(wall, port_name, peer):
    """把"这一端与另一堵墙共线续接"记到墙上（贴图据此走贯通纹）。"""
    note = getattr(wall, "note_inline", None)
    if callable(note):
        note(port_name, peer)


def _snap_offset(pa, pb, side_a, side_b, mode, cell=64.0):
    """按 port._snap_self_to_peer 的同一套算法，先算出"吸附会挪多少"。

    接头只应该把**本来就摆对位置**的两堵墙连起来（偏移 = 0）；不为 0 说明几何
    不自洽，这时绝不能连 —— 一连锁定就会把主动方（连同它已相连的结构）挪走。
    """
    ca, cb = _center(pa), _center(pb)
    if ca is None or cb is None:
        return None
    if mode == "loose" or side_a == side_b:
        return (cb[0] - ca[0], cb[1] - ca[1])
    if side_a in ("left", "right") and side_b in ("left", "right"):
        sign = -1.0 if side_a == "right" else 1.0
        return ((cb[0] + sign * cell) - ca[0], cb[1] - ca[1])
    if side_a in ("top", "bottom") and side_b in ("top", "bottom"):
        sign = -1.0 if side_a == "bottom" else 1.0
        return (cb[0] - ca[0], (cb[1] + sign * cell) - ca[1])
    return None


def apply_joint(joint, a, b, *, connect: bool = True, corners: bool = True, log=None):
    """按接头把两端的端口连起来（并把共线续接记在墙上）。"""
    if joint["kind"] == CORNER and not corners:
        return None
    if joint["kind"] == INLINE:
        # 端口能不能连上另说（对端可能已被北墙占满），共线这个事实先记下来
        _note_inline(a, joint["name_a"], b)
        _note_inline(b, joint["name_b"], a)
    if not connect:
        return None

    pa, pb = joint["port_a"], joint["port_b"]
    label = (f"{joint['kind']} {_tag(a)}.{joint['name_a']} <-> "
             f"{_tag(b)}.{joint['name_b']}")
    if pa.edges.get(joint["side_a"]) is pb and pb.edges.get(joint["side_b"]) is pa:
        return None                       # 这一对已经连着（重复同步），静默跳过
    if not pa.has_slot or not pb.has_slot:
        _log(log, f"[SKIP] {label}：端口已满（另一端已被占用）")
        return None
    if pa.edges.get(joint["side_a"]) is not None or pb.edges.get(joint["side_b"]) is not None:
        _log(log, f"[SKIP] {label}：该方向边已连")
        return None

    # 共线（格心重合）用 loose；端面相邻一格（成角 / 并排端接）用 exact ——
    # 两种情况下"本来就摆对了"都对应偏移 0，所以都不会挪动任何结构。
    mode = "loose" if joint["kind"] == INLINE else "exact"
    off = _snap_offset(pa, pb, joint["side_a"], joint["side_b"], mode)
    if off is None:
        _log(log, f"[SKIP] {label}：算不出吸附偏移，拒绝连接")
        return None
    if abs(off[0]) > 0.75 or abs(off[1]) > 0.75:
        _log(log, f"[REJECT] {label}：几何不自洽（吸附会挪动 "
                  f"({off[0]:.0f},{off[1]:.0f})px），不连")
        return None

    before = (_pos(a), _pos(b))
    try:
        res = pa.connect_to(pb, self_side=joint["side_a"], peer_side=joint["side_b"],
                            geometry=mode)
    except ConnectError as e:
        _log(log, f"[REJECT] {label}：{type(e).__name__}: {e}")
        return None
    except Exception as e:      # noqa: BLE001
        _log(log, f"[ERR] {label}：{type(e).__name__}: {e}")
        return None

    if before != (_pos(a), _pos(b)):
        _log(log, f"[WARN] {label}：接头把结构挪了位（几何不自洽，请查摆位）")
    _log(log, f"[OK] {label}")
    return res


def join_walls(walls, *, connect: bool = True, corners: bool = True, log=None):
    """把一组墙里所有"端面正对"的墙端按规则连起来（返回接头列表）。

    :param connect: 是否真的连端口。False = 只记录共线续接（不改拓扑）。
    :param corners: 是否处理成角接头（竖墙×横墙）。False = 只处理共线续接。
    """
    walls = [w for w in walls if w is not None and wall_kind(w) is not None]
    joints = []
    for i, a in enumerate(walls):
        for b in walls[i + 1:]:
            # 一对墙可能有【多处】合法接头（见 find_joints）：1 格宽的竖墙段常常
            # 上端贴一个角、下端贴另一个角。只取第一条会让另一端永远连不上。
            for j in find_joints(a, b):
                if j["kind"] == CORNER and not corners:
                    continue
                j["a"], j["b"] = a, b
                joints.append(j)
    # 返回"这次真的连上了"的那些（已连 / 端口满的会被跳过）
    if not connect:
        for j in joints:
            apply_joint(j, j["a"], j["b"], connect=False, corners=corners, log=log)
        return joints
    applied = []
    for j in joints:
        res = apply_joint(j, j["a"], j["b"], connect=True, corners=corners, log=log)
        if res is not None:
            applied.append(j)
    return applied


def prune_joints(targets, members, *, log=None):
    """把 targets 这些墙上"对端已经不在 members 里"的墙-墙接头拆掉。

    接头是**当前拓扑的函数**：走廊之间一断开，先前建立的墙-墙接头就该失效
    （不然墙头会一直停在贯通纹上，端口显示也还连着）。只动 targets 自己的墙端，
    不碰别人的接头 —— 否则一个窄视角的调用会把其他方向上合法的接头误拆。

    只看"对端是墙"的连接：连到地板/地板角/边缘口的那些（bottom↔floor、角口↔地板）
    不属于墙-墙接头，绝不在这里断开。
    """
    member_ids = {id(w) for w in members if w is not None}
    out = []
    for w in targets:
        kind = wall_kind(w)
        if kind is None:
            continue
        # ① 共线笔记：对端已不在这一组里 → 抹掉
        notes = getattr(w, "_inline_notes", None)
        if isinstance(notes, dict):
            for end, peer in list(notes.items()):
                if peer is not None and id(peer) in member_ids:
                    continue
                clear = getattr(w, "note_inline", None)
                if callable(clear):
                    clear(end, None)
                    out.append(("note", w, end))
        # ② 端口连接：对端是"墙"但不在 members 里 → 断开
        names = VWALL_END_PORTS if kind == "v" else HWALL_END_PORTS
        ports = getattr(w, "ports", {}) or {}
        for name in names:
            port = ports.get(name)
            if port is None or port.is_free:
                continue
            peers = list((getattr(port, "peers", {}) or {}).values())
            stale = [p for p in peers
                     if wall_kind(getattr(p, "structure", None)) is not None
                     and id(getattr(p, "structure", None)) not in member_ids]
            if stale and len(stale) == len(peers):
                for _p in peers:
                    try:
                        port.disconnect()
                    except Exception:      # noqa: BLE001
                        pass
                out.append(("port", w, name))
        refresh = getattr(w, "refresh_runs", None)
        if callable(refresh):
            refresh()
    if out and log is not None:
        log.append(f"[JOINT] 拆掉失效接头 {len(out)} 处（对端已不在这组墙里）")
    return out


# ------------------------------------------------------------------ #
# 场景级入口
# ------------------------------------------------------------------ #
def hall_side_walls(hall) -> list:
    """走廊自己的两道侧墙（南北向=左右竖墙，东西向=上下横墙）。"""
    out = []
    for name in ("left_wall", "right_wall", "top_wall", "bottom_wall"):
        part = hall.part(name) if hasattr(hall, "part") else None
        if part is not None and wall_kind(part) is not None:
            out.append(part)
    return out


def join_hall_neighbours(hall, *, connect: bool = True, corners: bool = True, log=None):
    """把这条走廊的侧墙与**已相连结构**的墙接起来（走廊串走廊、走廊接房子都走这里）。

    相连结构从 Structure.connections 里取（端口连接时互记过），不需要调用方
    说明"我跟谁挨着"；再走一跳取对端的邻居，避免把别处合法的接头判成失效。
    """
    mine = hall_side_walls(hall)
    peers = list(getattr(hall, "connections", []) or [])
    others = []
    for peer in peers:
        others.extend(hall_side_walls(peer))
        edge = getattr(peer, "_house_edges", None)
        if isinstance(edge, dict):          # 对端是房子：把补墙小段也算上
            for e in edge.values():
                others.extend(e.segments)
        for far in list(getattr(peer, "connections", []) or []):
            others.extend(hall_side_walls(far))
    members = mine + others
    prune_joints(mine, members, log=log)     # 先把失效的接头拆掉，再补新的
    return join_walls(members, connect=connect, corners=corners, log=log)


def join_house_edge(edge, *, connect: bool = True, corners: bool = True, log=None):
    """把房子周围的墙都接一遍：房子自己的墙 + **所有边上**的走廊侧墙 + 各边补墙段。

    为什么不是"只看这条边"：两条边上的墙会在角上相遇 —— 比如南边走廊的外侧墙
    正好顶在东边那段侧墙上（平行端接）。只把本边的走廊放进成员集，这种跨边的
    接头永远找不到对象（表现就是"hall 没和墙连起来"）。
    """
    house = edge.house
    members = hall_side_walls(house)                  # 房子自己的几面墙（拆掉的返回空）
    mine = []
    edges = getattr(house, "_house_edges", None)
    edge_list = list(edges.values()) if isinstance(edges, dict) else [edge]
    for e in edge_list:
        for hall in getattr(e, "halls", []) or []:
            mine.extend(hall_side_walls(hall))
        mine.extend(getattr(e, "segments", []) or [])
    members = members + mine
    prune_joints(mine, members, log=log)
    return join_walls(members, connect=connect, corners=corners, log=log)
