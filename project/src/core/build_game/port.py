# 说明：本文件内的行内注释曾在一次误操作的文本替换中损坏（编码事故），已清理。
# docstring 用同一份代码编译出的 .pyc 里的原文还原，措辞与旧版一致；
# 行内注释按当前实现重写。相关约定见 CALLBACK_SURFACE.md。
"""
port.py —— Structure 连接子系统（范式 v4：Port=格子上多向节点 + 边接口占用）

核心修正（对照 v3）
==================
v3 的错误：给每个 Port 实例发了 uuid / 按 (side, cell) 各建一个口 —— 这不是“节点”。
Port 的“ID”就是它的【类型】：观测时我们只关心“哪一类 Port 连上了哪一类 Port”。

本次模型（与 Structure 的设计理念同构，且吸收“边接口/网格节点”需求）：
1. 一个边界格 = 一个多向端口节点（Port 实例），节点可在这格的多个方向（边接口）被连接。
   - connectable_sides：本节点允许被连接的方向集合（如角口 ("top","left")、墙侧口 ("left","right")）。
   - edges[side]：该方向当前挂的对端 Port（None=此边空闲）。**每个方向至多连一次**。
   - max_connections：本节点的总连接上限。缺省 = len(connectable_sides)。

2. 候选池（candidate pool）仍不实例化 —— 候选关系只存在于全局规则表里，
   判断时用 edge_type(side) 查表，规则是唯一数据源。

3. 全局规则表：Key = 排序后的 Port 类型元组。一份规则写一遍。
   edge_type(side)：某方向实际用于查规则的“类型”——墙族方向型端口（竖墙头/尾）
   在不同的连接方向上是不同的角类型，因此查规则前先经 edge_type 归一化。

4. 连接判定（按需求，从严格到宽松，任一失败即驳回）：
   ① 满：connection_count >= max_connections → PortBusyError；
   ② 此边已连：edges[side] 非空 → PortBusyError；
   ③ 此边空闲 → 查规则表 (edge_type(self_side), edge_type(peer_side))；无规则 → RuleNotFoundError；
   ④ 都通过才绑定：mark 双方对应边，刷新状态，触发 on_connect，两侧结构各自适配/换图。

5. 标记状态（渲染叠加层）：0=空闲(绿) / 1=已连未满(黄) / 2=已满·占用(红)。
   连满即红 —— max_connections=1 的口一旦连上即“已满”，不再停留黄色。

6. 兼容层：一个端口节点可连【多个】对端 —— .peers（{方向: 对端 Port} 字典，按边实时）
   是唯一权威来源，.peer 与 .peers 同值；另有 .side（首个占用方向的只读属性）。
   需“首选对端”时读 .peers 的首个 value，遍历全部对端用 .peers.values()。

## 连接事务（一次 connect_to / disconnect 的完整副作用）

`connect_to` 是一次事务，顺序固定：

    ① 校验（容量 / 方向 / 规则 / 格尺寸）
    ② 绑定方向边 + 互记「谁连了谁」（Structure.connections）
    ③ 吸附：主动方整体平移，并**按接缝切一刀**级联带动它这一侧的所有结构
    ④ 逐对回调：两端各一次 on_port_changed(connected=True)
    ⑤ 批次回调：③④ 涉及的所有结构各一次 on_topology_settled（去重）

`disconnect` 与 ④⑤ 对称（connected=False）。要点：
- 换图的**唯一路径**是 on_port_changed / on_topology_settled，不在别处改精灵；
- 一次事务里任一结构最多收到一次 on_topology_settled（不再被重算 2~3 遍）；
- 被级联带动过的结构也收得到，否则它的贴图会停在旧坐标上。

详见 CALLBACK_SURFACE.md。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional
from src.core.command import Command

from utils.helpers import print_info


# ---------------------------------------------------------------------------
# 连接事务的公共出口：安全回调 + 批次结束通知
# ---------------------------------------------------------------------------
def _safe_call(obj, method: str, *args, sink=None, **kwargs) -> bool:
    """调 obj.method(*args)，方法不存在或抛异常都不中断连接事务。

    为什么吞异常：连接已经建立（端口已绑定），此时回调失败不该把结果回滚成"没连上"。
    但也**不能一声不响** —— 传 `sink`（一个 list）就把失败记进去，
    最终落到 PortConnectResult.warnings，作为"连上了但贴图没换"这类问题的线索。
    """
    fn = getattr(obj, method, None)
    if not callable(fn):
        return False
    try:
        fn(*args, **kwargs)
    except Exception as e:      # noqa: BLE001
        if sink is not None:
            sink.append(f"{type(obj).__name__}.{method}() 失败: "
                        f"{type(e).__name__}: {e}")
        return False
    return True


def _try_move(move_by, dx: float, dy: float, sink=None) -> bool:
    """调 move_by(dx, dy)；失败返回 False（不中断级联平移），可记进 sink。"""
    try:
        move_by(dx, dy)
    except Exception as e:      # noqa: BLE001
        if sink is not None:
            sink.append(f"{type(move_by).__name__} 平移失败: {type(e).__name__}: {e}")
        return False
    return True


def _settle(structures, sink=None) -> None:
    """一次连接/断开事务结束：给涉及的结构各发一次 on_topology_settled（去重）。

    :param structures: 本次事务涉及的结构（主动方、被动方、级联带动者）
    :param sink: 可选 list；回调失败记进这里（最终落到 PortConnectResult.warnings）
    """
    seen = set()
    for st in structures:
        if st is None or id(st) in seen:
            continue
        seen.add(id(st))
        _safe_call(st, "on_topology_settled", sink=sink)


def _reachable(root, *, block_nodes=None, block_pairs=None) -> list:
    """从 root 出发、沿 Structure.connections 能到达的结构（BFS，去重）。

    :param block_nodes: 这些结构**不展开**它的邻居（连它的邻居也不再往下走）
    :param block_pairs: 这些 {id 对} 之间的**边不通行**（结构本身还能展开），
                        用来挡住"本次刚建立的那条接缝"
    """
    blocked = set(block_nodes or ())
    pairs = set(block_pairs or ())
    out = []
    queue = [root]
    visited = set()
    while queue:
        st = queue.pop(0)
        if st is None or id(st) in visited:
            continue
        visited.add(id(st))
        out.append(st)
        if id(st) in blocked:
            continue
        for nb in list(getattr(st, "connections", []) or []):
            if nb is None:
                continue
            if (id(st), id(nb)) in pairs or (id(nb), id(st)) in pairs:
                continue
            queue.append(nb)
    return out


def _cascade_move(root, dx: float, dy: float, *, skip, sink=None) -> list:
    """把 root 及其**这一侧**的全部相连结构一起平移 (dx, dy)（保持相对位置）。

    要同时避免两个方向的错：
    · 只看一跳：走廊串成 h1-h2-h3-h4 时，接 h4 只带动 h3，h2/h1 留在原地
      —— 整串被拉断。
    · 带得太多：接缝对面（本次吸附的静止对端）自己还挂着一大家子
      （例如房子还接着别的走廊），把它们一起拖走就把整个场景拉散。

    判据 = **按接缝切一刀**：把"本次刚建立的那条边"当作不可通行，然后

        root 侧 = 从 root 可达（跨不过接缝）

    为什么必须禁的是**边**而不是节点：`_snap_self_to_peer` 跑的时候，
    root 与静止对端之间那条连接**已经建好了**（connect_to 里先 connect_with +
    record_connection，再吸附）。它是双向的 —— 只把对端拉黑没用，从对端那头
    也能走回 root；只禁节点又会让对端那一族从另一条路绕进来。禁这一对边才切得干净。

    :param skip: 本次吸附的静止对端（接缝那一侧，不移动）
    :param sink: 可选 list；平移失败记进这里
    :return: root 这一侧被移动过的结构列表（含 root 自身）
    """
    peers = [s for s in (skip or ()) if s is not None]
    block_pairs = {(id(root), id(p)) for p in peers}

    side = _reachable(root, block_nodes={id(p) for p in peers},
                      block_pairs=block_pairs)
    moved = []
    for st in side:
        if any(st is p for p in peers):
            continue                        # 静止对端：不动
        move = getattr(st, "move_by", None)
        if callable(move) and _try_move(move, dx, dy, sink=sink):
            moved.append(st)
    return moved


# ---------------------------------------------------------------------------
# Structure 实例 ID 工具（记录"谁连了谁"用；Port 自身不带独立 ID）
# ---------------------------------------------------------------------------
def new_instance_id() -> str:
    """为 Structure 实例生成短唯一 ID（观察输出里区分是哪面墙连的）。"""
    import uuid
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 连接异常
# ---------------------------------------------------------------------------
class ConnectError(Exception):
    """连接流程中的基础异常。"""


class RuleNotFoundError(ConnectError):
    """某对 Port 类型在全局规则表中没有注册（无法判定是否合法）。"""


class GeometryInvalidError(ConnectError):
    """几何验证不通过。"""


class ScaleMismatchError(GeometryInvalidError):
    """缩放因子不同 → 不允许直接连接，需要适配器(Adapter)。"""


class ShapeIncompatibleError(GeometryInvalidError):
    """接触面轮廓不吻合（如 1 格凸插不进 2 格凹）。"""


class PortBusyError(ConnectError):
    """连接被占用驳回：已满 或 该方向边已连接。"""


# ---------------------------------------------------------------------------
# Port：格子上多向节点（子类即类型 / 边接口）
# ---------------------------------------------------------------------------
class Port:
    """
    Port 基类 —— 一个边界格处的多向连接节点。

    子类即类型：
    - 每个具体 Port 子类通过类属性 `port_type` 声明自己的类型字符串，
      例如 HorizontalWallTopLeftPort.port_type = "HorizontalWallTopLeft"。
    - `connectable_sides`：本节点可被连接的方向集合（边接口），子类按需覆写。
    - `max_connections`：总连接上限。None=缺省取 len(connectable_sides)。
    - 方向型端口（竖墙头/尾）覆写 `edge_type(side)`：不同方向返回不同角类型，供查规则。

    候选关系不自带：要判断"这个 Port 能连哪些类型"，查全局规则表：
      port.candidates()               # 从规则表反查所有可连的 Port 类型
    """

    # 子类覆写：本 Port 的类型字符串。缺省退回类名，保证总有可观测类型。
    port_type: Optional[str] = None

    # 子类覆写：本 Port 允许的最大连接数（None=缺省取可连方向数）。
    max_connections: Optional[int] = None

    # 子类覆写：本节点可被连接的方向（边接口）集合。缺省四个方向。
    connectable_sides: tuple[str, ...] = ("top", "bottom", "left", "right")

    # 轻量实例字段：宿主结构 + 所在格 + 状态 + 各方向占用（无缓存；对端一律按边推导）
    __slots__ = ("structure", "index", "cell", "status", "edges")

    def __init__(self, structure, index: int, cell: tuple[int, int],
                 max_connections: Optional[int] = None,
                 connectable_sides: Optional[tuple[str, ...]] = None):
        self.structure = structure      # 宿主 Structure
        self.index = index              # 沿首次发现的边序号（观测用）
        self.cell = tuple(cell)         # 所在边界格 (gx, gy)
        if connectable_sides is not None:
            self.connectable_sides = tuple(connectable_sides)
        if max_connections is not None:
            self.max_connections = int(max_connections)
        elif self.max_connections is None:
            self.max_connections = len(self.connectable_sides)
        self.edges: dict[str, Optional["Port"]] = {side: None for side in self.connectable_sides}
        self.status = "free"

    # ---- 类型（这就是"Port 的 ID"） ----
    @property
    def type(self) -> str:
        cls_port_type = type(self).port_type
        if cls_port_type is None:
            return type(self).__name__
        return cls_port_type

    @property
    def host_type(self) -> str:
        """宿主 Structure 的类型（观测用：哪个结构的哪个口）。"""
        return self.structure.structure_type

    # ---- 方向 / 边接口 ----
    def edge_type(self, side: str) -> str:
        """某个方向用于查规则表的"类型"。默认即本节点类型。

        方向型端口（如竖墙头/尾，在同一格的不同方向上是不同角词汇）覆写本方法，
        依据 side 返回不同的角类型（如 side=left → "HorizontalWallTopLeft"）。
        """
        return self.type

    @property
    def side(self) -> Optional[str]:
        """只读兼容属性：首个已占用边的方向；未占用则返回首个可连方向。"""
        for s in self.connectable_sides:
            if self.edges.get(s) is not None:
                return s
        return self.connectable_sides[0] if self.connectable_sides else None

    @property
    def peers(self) -> dict:
        """已连接对端字典：{方向(side): 对端 Port}，按边实时推导，永不过期。

        一个端口节点可连【多个】对端（如地板角口 max=2 挂两堵墙，各占一个方向）；
        字典的 key 是【哪个方向】连了【哪一侧】，value 是那个方向上的对端 Port。
        ``edges`` 本身已是 side→peer 映射，这里只是过滤掉未占用(None)的边。
        """
        return {side: p for side, p in self.edges.items() if p is not None}

    def connected_sides(self) -> list[str]:
        """当前已连接（占用）的方向边列表。"""
        return [s for s in self.connectable_sides if self.edges.get(s) is not None]

    def edge_connected(self, side: str) -> bool:
        """某个方向边是否已被连接。"""
        return self.edges.get(side) is not None

    # ---- 状态 ----
    @property
    def connection_count(self) -> int:
        """已连接的方向边数（0..max_connections）。"满/未满"判据。"""
        return sum(1 for e in self.edges.values() if e is not None)

    @property
    def _at_capacity(self) -> bool:
        """已达最大连接数（满/占用）。"""
        return self.connection_count >= self.max_connections

    @property
    def has_slot(self) -> bool:
        """还有空槽可再连一个（墙-墙接头会先问这个再决定要不要连）。"""
        return self.connection_count < self.max_connections

    @property
    def is_free(self) -> bool:
        return self.connection_count == 0

    @property
    def is_connected(self) -> bool:
        return self.connection_count > 0

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"

    @property
    def marker_state(self) -> int:
        """端口标记状态（供渲染器叠加层绘制）：
        0 = 空闲(绿) / 1 = 已连接未满(黄) / 2 = 已满·占用(红)。

        "连满即红"：max_connections=1 的口一旦连上即已满 → 红(占用)，
        不再有"max=1 连上永远黄"的旧约定；多边口连 1 个仍未满 → 黄，连满 → 红。
        """
        n = self.connection_count
        if n == 0:
            return 0
        if self._at_capacity:
            return 2
        return 1

    def _refresh_status(self):
        """按当前连接数派生 status：0=free / 满=locked / 其余=connected。"""
        n = self.connection_count
        if n == 0:
            self.status = "free"
        elif self._at_capacity:
            self.status = "locked"
        else:
            self.status = "connected"

    # ---- 候选池：从全局规则表反查（不实例化任何候选对象） ----
    def candidates(self, registry: "RuleRegistry" = None):
        """
        从全局规则表反查：本 Port 的类型可以连接哪些其它 Port 类型。
        候选永远与规则表一致 —— 规则是唯一数据源。
        """
        reg = registry if registry is not None else RULES
        return reg.candidates_for(self.type)

    # ---- 连接原语（仅绑定，不做规则/满/边校验） ----
    def connect_with(self, peer: "Port", self_side: str, peer_side: str):
        """把本 Port 沿 self_side 边 与对端沿 peer_side 边 互相绑定。

        调用方（connect_to）已保证：未满、本边空闲、规则存在。
        绑定后刷新双方状态；若占满则双方转 LOCKED。
        """
        if self.edges.get(self_side) is not None or peer.edges.get(peer_side) is not None:
            raise PortBusyError(
                f"port busy (edge already connected): "
                f"{self!r}[{self_side}] ↔ {peer!r}[{peer_side}]")
        self.edges[self_side] = peer
        peer.edges[peer_side] = self
        self._refresh_status()
        peer._refresh_status()

    # ---- 端口级连接入口（连接单元 = 端口节点 + 方向边） ----
    def connect_to(self, peer: "Port", registry: "RuleRegistry" = None,
                   self_side: str = None, peer_side: str = None,
                   geometry: str = "exact") -> "PortConnectResult":
        """
        端口级连接第一入口：**端口节点(某方向边) ↔ 端口节点(某方向边)**。

        self_side / peer_side：**必填**，指定用哪条方向边去连。判定顺序（按需求）：
        ① 满：双方任一 connection_count >= max_connections → PortBusyError（驳回：已满）；
        ② 此边已连：任一侧该方向边已占用 → PortBusyError（驳回：此边已连接）；
        ③ 此边空闲 → 查规则表 (edge_type(self_side), edge_type(peer_side))；
           无规则 → RuleNotFoundError（驳回：连接类型未注册）；
        ④ 绑定：connect_with 锁定该方向，刷新状态，触发该规则 on_connect 回调；
        ⑤ 观测+适配：记录实例连接，主动方(self 宿主) 平移贴靠对端并级联带动已相连结构。

        geometry：几何适配模式 —— "exact"（默认）/ "loose"
          · "exact"（默认）：按对端格心 + 自身格宽/高做"贴边"偏移；
            两端都声明了格尺寸且不一致时 → ScaleMismatchError（需要适配器）；
          · "loose"（非几何检测）：不做任何尺寸/跨度校验，直接把端口锚定格
            叠到对端锚定格上（格心重合），偏移交给调用方自己调。
            用于"房子开口宽度 ≠ 走廊开口宽度"这类跨尺寸拼接。
        """
        if self_side is None or peer_side is None:
            raise ValueError(
                "connect_to(peer) 需要显式给出两侧方向："
                f"connect_to(peer, self_side={self_side!r}, peer_side={peer_side!r})。")
        reg = registry if registry is not None else RULES
        geometry = str(geometry or "exact").lower()
        if geometry not in ("exact", "loose"):
            raise ValueError(
                f"geometry 必须是 'exact' / 'loose'，"
                f"收到 {geometry!r}")

        # 方向合法性：该方向必须是本节点可连的边接口
        if self_side not in self.connectable_sides:
            raise ValueError(
                f"{self!r} 节点不可从方向 {self_side!r} 连接"
                f"（可连方向: {self.connectable_sides}）。")
        if peer_side not in peer.connectable_sides:
            raise ValueError(
                f"{peer!r} 节点不可从方向 {peer_side!r} 连接"
                f"（可连方向: {peer.connectable_sides}）。")

        # ③.5 几何校验（仅 exact 模式）：两端格尺寸必须一致
        if geometry == "exact":
            self._check_scale(peer)

        # ① 已满 → 驳回
        if self._at_capacity or peer._at_capacity:
            raise PortBusyError(
                f"port busy (at capacity): {self!r} status={self.status} "
                f"({self.connection_count}/{self.max_connections}), "
                f"{peer!r} status={peer.status} ({peer.connection_count}/{peer.max_connections})")

        # ② 此边已连接 → 驳回
        if self.edges.get(self_side) is not None:
            raise PortBusyError(
                f"port busy (this edge already connected): "
                f"{self!r} 边 {self_side!r} 已连 {self.edges[self_side]!r}")
        if peer.edges.get(peer_side) is not None:
            raise PortBusyError(
                f"port busy (this edge already connected): "
                f"{peer!r} 边 {peer_side!r} 已连 {peer.edges[peer_side]!r}")

        # ③ 检查连接类型 → 规则表
        type_a = self.edge_type(self_side)
        type_b = peer.edge_type(peer_side)
        rule = reg.get(type_a, type_b)
        if rule is None:
            raise RuleNotFoundError(
                f"端口对未注册规则，连接被驳回："
                f"({type_a}, {type_b})。请先 register_rule。")

        # ④ 绑定
        self.connect_with(peer, self_side, peer_side)
        rule.fire_on_connect(self, peer)   # 仅观测

        # 互记「谁连了谁」（Structure 实例级）：级联联动依赖 A.connections
        sa = getattr(self, "structure", None)
        sb = getattr(peer, "structure", None)
        warnings: list = []
        if sa is not None and sb is not None:
            try:
                sa.record_connection(sb)
                sb.record_connection(sa)
            except Exception as e:      # noqa: BLE001
                warnings.append(f"互记连接失败: {type(e).__name__}: {e}")

        # 适配：调用方(self 所在结构 A)自身整体平移去贴 peer(B) —— B 静止；
        # A 移动时按接缝切一刀，级联带动它这一侧的相连结构。
        moved = self._snap_self_to_peer(peer, self_side, peer_side, geometry,
                                        sink=warnings)

        # 逐对回调：两端各一次（业务换图由 Structure → sprite 自决）
        _safe_call(sa, "on_port_changed", self, peer, self_side, peer_side,
                   connected=True, sink=warnings)
        _safe_call(sb, "on_port_changed", peer, self, peer_side, self_side,
                   connected=True, sink=warnings)

        # 批次结束回调：本次事务里动过的所有结构各一次（去重）——
        # 被级联带动的位置变了，它们的贴图要按新坐标重算。
        _settle([sa, sb, *moved], sink=warnings)

        return PortConnectResult(
            ok=True, active=self, passive=peer, connected=[(self, peer, rule)],
            warnings=warnings)

    # ---- 端口的格子世界中心（用于吸附几何） ----
    def cell_center_world(self) -> tuple[float, float]:
        """取本端口所在格的像素中心（世界坐标）。

        优先用 Structure 的格→像素换算；缺省退回该格精灵的 world_position.center。
        """
        st = self.structure
        calc = getattr(st, "_calc_pixel_position", None)
        if callable(calc):
            try:
                return calc(self.cell)
            except Exception:
                pass
        sprite = getattr(st, "_sprite_at_port", None)
        if callable(sprite):
            s = sprite(self)
            wp = getattr(s, "world_position", None)
            if wp is not None:
                return (getattr(wp, "centerx", 0), getattr(wp, "centery", 0))
        return (0.0, 0.0)

    # ---- 适配：调用方(自身 A)整体移动贴合对端，并按接缝级联 ----
    def _check_scale(self, peer: "Port") -> None:
        """exact 模式下的几何校验：两端宿主结构声明的格尺寸必须一致。

        两端都必须声明 `_cell_w/_cell_h` 才校验 —— 组合结构（House/Hall）本身没有
        单一网格，由子部件承载尺寸，这类跨结构拼接一律交给调用方决定是否用 loose。
        """
        sa = getattr(self, "structure", None)
        sb = getattr(peer, "structure", None)
        if sa is None or sb is None:
            return
        a = (getattr(sa, "_cell_w", None), getattr(sa, "_cell_h", None))
        b = (getattr(sb, "_cell_w", None), getattr(sb, "_cell_h", None))
        if None in a or None in b:
            return
        if abs(float(a[0]) - float(b[0])) > 0.5 or abs(float(a[1]) - float(b[1])) > 0.5:
            raise ScaleMismatchError(
                f"格尺寸不一致，exact 几何无法直连：{type(sa).__name__} cell={a} "
                f"vs {type(sb).__name__} cell={b}。"
                f"需要适配器，或改用 connect_to(..., geometry='loose') 走非几何检测连接。")

    def _snap_self_to_peer(self, peer: "Port", side_a: str, side_b: str,
                           geometry: str = "exact", sink=None) -> list:
        """调用方 self 所在结构(A)整体平移，让 A 的端口格沿接缝贴到 peer(B) 端口格外侧。

        B 保持静止；计算出的平移量 (dx, dy) 作用在 A 自身，以及 A 这一侧的相连结构
        （保持相对位置一起平移）。

        连接朝向由两侧实际使用的方向边（互补）决定：
        - exact: left↔right 沿 X 拉齐（A 格中心 = B 格中心 ∓ 一格宽）；top↔bottom 沿 Y 同理；
        - loose: 不做格宽偏移，直接把 A 的端口锚定格【叠】到 B 的锚定格上（格心重合），
                 于是"开口宽度不一样"的两端也能接上，位置由调用方再微调。

        :return: 本次**真的被移动过**的结构列表（A 自身 + 级联带动者）。
                 调用方据此决定要给哪些结构补发 on_topology_settled ——
                 位置变了，它们的贴图要按新坐标重算。
        """
        sa, sb = getattr(self, "structure", None), getattr(peer, "structure", None)
        if sa is None or sb is None:
            return []
        move_self = getattr(sa, "move_by", None)
        if not callable(move_self):
            return []

        a = self.cell_center_world()
        b = peer.cell_center_world()

        # 每格宽/高（缺省 64，取 A 自己的格尺寸）
        cw = float(getattr(sa, "_cell_w", 64))
        ch = float(getattr(sa, "_cell_h", 64))

        dx = dy = 0.0
        try:
            if str(geometry).lower() == "loose":
                # 非几何检测：锚定格直接重合，不按格宽做"贴边"
                dx = b[0] - a[0]
                dy = b[1] - a[1]
            elif side_a in ("left", "right") and side_b in ("left", "right"):
                sign = -1.0 if side_a == "right" else 1.0
                ttx = b[0] + sign * cw
                tty = b[1]
                dx = ttx - a[0]
                dy = tty - a[1]
            elif side_a in ("top", "bottom") and side_b in ("top", "bottom"):
                sign = -1.0 if side_a == "bottom" else 1.0
                tty = b[1] + sign * ch
                ttx = b[0]
                dx = ttx - a[0]
                dy = tty - a[1]
        except Exception:
            dx = dy = 0.0

        if dx == 0.0 and dy == 0.0:
            return []

        # 移动 A 自身 + A 这一侧的全部相连结构；对端 B 保持静止。
        return _cascade_move(sa, dx, dy, skip={sb}, sink=sink)

    def disconnect(self, side: Optional[str] = None):
        """断开连接：同步对端、刷新状态，并触发两端 Structure 的纹理更新回调。

        - side=None：断开本节点【全部】方向边的连接；
        - side="top"/"bottom"/"left"/"right"：仅断开该方向的一条边（多连接节点用）。

        每次断开都会：
          ① 清空对端回指本节点的边（对端字典 .peers / .peer 按边实时更新，无需缓存）；
          ② 刷新两端 Port 状态；
          ③ 从两端 Structure 的 connections 中移除彼此（若不再相连）；
          ④ 两端 Structure 各收一次 on_port_changed(connected=False)，
             随后各收一次 on_topology_settled（子类据端口簇重新决定贴图）。
        """
        sides = [side] if side is not None else list(self.edges.keys())
        for s in sides:
            self._disconnect_side(s)
        self._refresh_status()

    def _disconnect_side(self, side: str) -> None:
        """断开本节点的单条方向边，并同步对端（不刷新本节点整体状态，由调用方 refresh）。"""
        peer = self.peers.get(side, None)
        if peer is None:
            return
        # ① 找到对端指向本节点的边并清空
        peer_side = None
        for ps in list(peer.edges.keys()):
            if peer.edges.get(ps) is self:
                peer.edges[ps] = None
                peer_side = ps
        peer._refresh_status()
        self.edges[side] = None
        # ③/④ 通知两端 Structure：解绑实例级连接 + 逐对断开回调 + 批次结束回调
        self._notify_structure_disconnect(peer, side, peer_side)

    def _notify_structure_disconnect(self, peer: "Port", side: str,
                                     peer_side: Optional[str]) -> None:
        """断开一对 Port(side ↔ peer_side)后，通知两端宿主 Structure 解绑并重算纹理。"""
        sa = getattr(self, "structure", None)
        sb = getattr(peer, "structure", None)
        if sa is None or sb is None:
            return
        # 仅当两端不再有任何直连 Port 时才移除实例级连接（多连接节点可能仍相连）
        if not self._structures_still_connected(sa, sb):
            for host, other in ((sa, sb), (sb, sa)):
                conns = getattr(host, "connections", None)
                if isinstance(conns, list) and other in conns:
                    conns.remove(other)
        # 逐对断开回调：两端各一次（与连接侧对称）
        _safe_call(sa, "on_port_changed", self, peer, side, peer_side,
                   connected=False)
        _safe_call(sb, "on_port_changed", peer, self, peer_side, side,
                   connected=False)
        # 批次结束回调：两端各一次（去重）
        _settle([sa, sb])

    @staticmethod
    def _structures_still_connected(sa, sb) -> bool:
        """sa 与 sb 之间是否仍有（任何一对）直连 Port。"""
        try:
            for p in sa.get_all_ports():
                for q in p.peers.values():
                    if getattr(q, "structure", None) is sb:
                        return True
        except Exception:
            pass
        return False

    # ---- 观测输出 ----
    def describe(self) -> str:
        """观测文本：哪个结构(#实例)的哪个口(类型) —— 即"谁连了哪一类"。"""
        return f"{self.host_type}#{self.structure.instance_id} {self.side}[{self.index}] {self.type}"

    def short(self) -> str:
        """简短观测：只标 哪个墙(类型) 的 哪个位置 —— 例如 wall.top[0]。"""
        return f"{self.host_type}.{self.side}[{self.index}]"

    def __repr__(self):
        return (f"{type(self).__name__}({self.describe()}, "
                f"status={self.status}, {self.connection_count}/{self.max_connections}, "
                f"sides={self.connected_sides()})")


class OverlapMixinPort(Port):
    """给 Port 子类混入重叠连接能力。

    `_snap_self_to_peer` 与父类同签名（多了 geometry）—— 连接入口统一按
    `(peer, side_a, side_b, geometry)` 调用；覆写时漏掉 geometry 会让重叠连接
    直接 TypeError（被 Structure 的 _try 吞掉），表现为"墙没被吸附、停在原始位置"。
    返回值同样是"被移动过的结构列表"（父类用它补发 on_topology_settled）。
    """

    def _snap_self_to_peer(self, peer, side_a, side_b, geometry="exact", sink=None):
        # 1. 提取宿主结构
        sa = getattr(self, "structure", None)
        sb = getattr(peer, "structure", None)
        if sa is None or sb is None:
            return []

        move_self = getattr(sa, "move_by", None)
        if not callable(move_self):
            return []

        # 2. 获取两端口格子的世界中心
        a = self.cell_center_world()
        b = peer.cell_center_world()

        # 3. 核心判定：同向连接（如 right↔right, top↔top）或 loose 模式 -> 完全重叠
        if str(geometry).lower() == "loose" or side_a == side_b:
            dx = b[0] - a[0]
            dy = b[1] - a[1]
        else:
            # 否则走原来的互补贴靠逻辑（复制自父类 Port._snap_self_to_peer）
            cw = float(getattr(sa, "_cell_w", 64))
            ch = float(getattr(sa, "_cell_h", 64))
            if side_a in ("left", "right") and side_b in ("left", "right"):
                sign = -1.0 if side_a == "right" else 1.0
                dx = (b[0] + sign * cw) - a[0]
                dy = b[1] - a[1]
            elif side_a in ("top", "bottom") and side_b in ("top", "bottom"):
                sign = -1.0 if side_a == "bottom" else 1.0
                dy = (b[1] + sign * ch) - a[1]
                dx = b[0] - a[0]
            else:
                dx, dy = 0.0, 0.0

        if dx == 0.0 and dy == 0.0:
            return []

        # 4. 移动 A 自身 + A 这一侧的全部相连结构；对端 B 保持静止。
        return _cascade_move(sa, dx, dy, skip={sb}, sink=sink)


# ---------------------------------------------------------------------------
# 规则包：两个 Port 类型可连接 + 连接成功后触发什么回调
# ---------------------------------------------------------------------------
class ConnectRule:
    """
    一份规则 = 一对可连接的 Port 类型 + 连接后的回调。注册一次，双向复用。

    - port_type_a / port_type_b : 参与连接的 Port 类型（内部归一化排序）
    - action                    : 动作标签（如 "docking"）。仅作观测/存档记录，
                                  不再广播给结构（见 CALLBACK_SURFACE.md 批次 1）。
    - on_connect                : 可选回调 on_connect(port_a, port_b)，
                                  每成功连接一对该类型的 Port 就调用一次。
                                  回调只负责“观测/触发”，贴图仍由 Structure 自己解析。
    """

    def __init__(self, port_type_a: str, port_type_b: str,
                 action: str = "docking",
                 on_connect: Optional[Callable[["Port", "Port"], None]] = None):
        self.type_a, self.type_b = sorted((str(port_type_a), str(port_type_b)))
        self.action = action
        self._on_connect = on_connect

    @property
    def key(self) -> tuple[str, str]:
        return (self.type_a, self.type_b)

    def covers(self, type_x: str, type_y: str) -> bool:
        return sorted((type_x, type_y)) == [self.type_a, self.type_b]

    def fire_on_connect(self, port_a: Port, port_b: Port):
        if self._on_connect is not None:
            self._on_connect(port_a, port_b)

    def __repr__(self):
        return f"ConnectRule(key={self.key!r}, action={self.action!r})"


class RuleRegistry:
    """
    全局单点规则表（候选池的唯一来源）。
    Key = 排序后的 Port 类型元组 → ConnectRule。
    """

    def __init__(self):
        self._rules: dict[tuple[str, str], ConnectRule] = {}

    def register(self, rule: ConnectRule, overwrite: bool = False) -> ConnectRule:
        """注册规则。一份规则写一遍：重复 Key 默认抛错（除非 overwrite=True）。"""
        if rule.key in self._rules and not overwrite:
            raise ValueError(
                f"rule already registered for {rule.key!r}. "
                f"一份规则写一遍：请勿重复注册；如需覆盖请传 overwrite=True。")
        self._rules[rule.key] = rule
        return rule

    def get(self, type_a: str, type_b: str) -> Optional[ConnectRule]:
        return self._rules.get(tuple(sorted((type_a, type_b))))

    def candidates_for(self, port_type: str) -> list[str]:
        """
        反查：给定一个 Port 类型，规则表里能与之连接的所有其它 Port 类型。
        （这就是“候选池”——不实例化，直接从规则推导，永远与规则一致。）
        """
        result = set()
        for (ta, tb) in self._rules:
            if port_type == ta:
                result.add(tb)
            elif port_type == tb:
                result.add(ta)
        return sorted(result)

    def unregister(self, type_a: str, type_b: str):
        self._rules.pop(tuple(sorted((type_a, type_b))), None)

    def clear(self):
        self._rules.clear()

    def __contains__(self, key):
        return tuple(sorted(key)) in self._rules

    def __len__(self):
        return len(self._rules)


# 全局默认规则表（也可自建 RuleRegistry 实例隔离规则域）
RULES = RuleRegistry()
Command.register(RULES)


def register_rule(port_type_a: str, port_type_b: str, *,
                  registry: Optional[RuleRegistry] = None,
                  **kwargs) -> ConnectRule:
    """
    便捷注册：register_rule("HorizontalWallTopLeft", "HorizontalWallTopRight",
                            action="docking", on_connect=my_callback)
    返回创建的 ConnectRule。
    """
    reg = registry or RULES
    rule = ConnectRule(port_type_a, port_type_b, **kwargs)
    reg.register(rule)
    return rule


# ---------------------------------------------------------------------------
# 连接结果
# ---------------------------------------------------------------------------
@dataclass
class PortConnectResult:
    """端口级连接(port.connect_to)的结果：只针对明确指定的一对端点。

    `warnings`：连接**已经成立**、但过程中某些回调/级联平移失败时记在这里。
    之所以不抛异常：端口已经绑定，此时回滚成"没连上"更糟；但也**不能一声不响** ——
    这是排障"连接上了但贴图没换"这类问题的第一手线索。
    """
    ok: bool
    active: "Port"
    passive: "Port"
    connected: list = field(default_factory=list)      # [(port_a, port_b, rule), ...]
    warnings: list = field(default_factory=list)       # [str, ...]

    @property
    def rule(self):
        return self.connected[0][2] if self.connected else None

    def summary(self) -> str:
        if not self.connected:
            return "未连"
        pa, pb, _ = self.connected[0]
        base = f"{pa.short()} ↔ {pb.short()}"
        return base + (f"（{len(self.warnings)} 条告警）" if self.warnings else "")
