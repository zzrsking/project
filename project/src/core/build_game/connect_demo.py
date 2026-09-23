"""
connect_demo.py —— port 连接子系统 自测/演示 v3（无显示、无资源依赖，可 headless 运行）

覆盖验证（范式 v3）：
  0. Port 是类型化轻量基类：HorizontalWall 四角的 Port 是四个不同子类，子类即类型；
     候选池不实例化 —— Port.candidates() / can_connect_to() 从全局规则表反查。
  1. 规则表以 Port 类型对为 Key；一份规则写一遍，注册一次双向复用；
     重复注册同一类型对 → 被拦截。
  2. 未注册类型对（如 door 的角）→ RuleNotFoundError（整线无规则时驳回）。
  3. 几何适配：缩放因子不同 → ScaleMismatchError（适配器事件）；
     轮廓不咬合（高度不同） → ShapeIncompatibleError。
  4. 联动连接：两条墙右↔左贴靠，两对对角 Port 批量连接；
     主动方移动、被动方原地不动、双方互记实例、两端收到 action。
  5. 规则 on_connect 回调：每成功连接一对 Port 触发一次（观测“谁连了哪一类”）。
  6. 已锁定 Port 拒绝重复连接（PortBusyError）。
  7. 竖着叠放（bottom↔top）也复用同一套按角注册的规则。
  8. 显式隔离域（自建 RuleRegistry）不污染全局表。
"""

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from src.core.build_game.horizontalwall import (
    HorizontalWall,
    HorizontalWallTopLeftPort, HorizontalWallTopRightPort,
    HorizontalWallBottomLeftPort, HorizontalWallBottomRightPort,
)
from src.core.build_game.port import (
    RULES, register_rule,
    PortBusyError, RuleNotFoundError, ScaleMismatchError, ShapeIncompatibleError,
)

PASS = 0
FAIL = 0


# ---------------------------------------------------------------------------
# 本 demo 专用的「沿边扫描批量连接」小工具
#
# 为什么放在这里而不是 port.py：它只被本 demo 使用（引擎其余部分一律走
# `Port.connect_to` 这一对一入口）。放在 port.py 里会让人以为还有第二条连接路径
# —— 见 CALLBACK_SURFACE.md 批次 1：无调用方的宽松公共接口一律收掉。
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field


@dataclass
class ConnectResult:
    """一次批量沿边连接的完整结果（demo 观测用）。"""
    ok: bool
    active: object
    passive: object
    side_a: str
    side_b: str
    offset: tuple = (0.0, 0.0)
    connected: list = field(default_factory=list)      # [(port_a, port_b, rule), ...]
    skipped: list = field(default_factory=list)

    def summary(self) -> str:
        obs = ", ".join(f"{pa.short()}↔{pb.short()}" for pa, pb, _ in self.connected)
        return (f"{self.side_a}→{self.side_b} 连了{len(self.connected)}对口"
                + (f": {obs}" if obs else ""))


def connect(active, passive, side_a: str, side_b: str, geometry: str = "exact"):
    """结构↔结构 的批量「沿边扫描」连接：逐格对齐后把两侧端口两两 connect_to。

    geometry="loose" 时不做格尺寸校验（用于演示"缩放不同也能接"）。
    """
    pa_list = active.get_side_ports(side_a)
    pb_list = passive.get_side_ports(side_b)
    connected: list = []
    for i in range(min(len(pa_list), len(pb_list))):
        pa, pb_ = pa_list[i], pb_list[i]
        if pa is None or pb_ is None:
            continue
        res = pa.connect_to(pb_, self_side=side_a, peer_side=side_b, geometry=geometry)
        if res.ok:
            connected.extend(res.connected)
    return ConnectResult(ok=bool(connected), active=active, passive=passive,
                         side_a=side_a, side_b=side_b, connected=connected)


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def register_wall_corner_rules(log=None):
    """一份规则写一遍：四个角 Port 类型两两配对 + 连接回调(观测谁连了谁)。"""
    def make_cb():
        def cb(a, b):
            if log is not None:
                log.append(f"{a.describe()} ↔ {b.describe()}")
        return cb

    return [
        register_rule("HorizontalWallTopLeft", "HorizontalWallTopRight",
                      action="docking", on_connect=make_cb()),
        register_rule("HorizontalWallBottomLeft", "HorizontalWallBottomRight",
                      action="docking", on_connect=make_cb()),
        register_rule("HorizontalWallBottomLeft", "HorizontalWallTopLeft",
                      action="docking", on_connect=make_cb()),
        register_rule("HorizontalWallBottomRight", "HorizontalWallTopRight",
                      action="docking", on_connect=make_cb()),
    ]


class ActionSpyWall(HorizontalWall):
    """演示 Structure 自行维护纹理：记录收到的连接回调。

    注：原先记录的 `on_connection_action(rule.action)` 已随命令通道一并移除
    （见 CALLBACK_SURFACE.md 批次 1）—— 规则里的 action 只留作观测，不再广播给结构。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_actions = []

    def on_port_changed(self, port_a, port_b, side_a, side_b, *, connected=True):
        if connected:
            self.received_actions.append("connect")
        super().on_port_changed(port_a, port_b, side_a, side_b, connected=connected)


def main():
    RULES.clear()

    print("== 0. Port 是类型子类，候选池从规则表反查 ==")
    a0 = HorizontalWall((4, 3), (64, 64), center=(0, 0))
    right_ports = a0.get_side_ports("right")          # 右列边界格：[(3,y)]
    tr = right_ports[0]                               # 第 0 行 = 右上角格
    br = right_ports[-1]                              # 最后一行 = 右下角格
    check("右上角格挂的是 TopRightPort 子类",
          isinstance(tr, HorizontalWallTopRightPort), repr(tr))
    check("右下角格挂的是 BottomRightPort 子类",
          isinstance(br, HorizontalWallBottomRightPort), repr(br))
    check("Port 类型即子类声明(无独立uuid)",
          tr.type == "HorizontalWallTopRight" and br.type == "HorizontalWallBottomRight",
          f"tr={tr.type}, br={br.type}")
    # 未注册任何规则时：候选池为空（不实例化，仅查表）
    check("未注册规则前 candidates() 为空", tr.candidates() == [], str(tr.candidates()))

    print("\n== 1. 规则表以 Port 类型对为 Key，一份规则写一遍 ==")
    obs = []
    register_wall_corner_rules(log=obs)
    # 角 Port 位于两条边的交点上，因此可连的对角类型不止一个：
    # TopRight 既可在右缝连 TopLeft，也可在顶缝连 BottomRight。
    check("注册后反查候选池: TopRight 可连 {TopLeft, BottomRight}",
          tr.candidates() == ["HorizontalWallBottomRight", "HorizontalWallTopLeft"],
          str(tr.candidates()))
    check("注册后反查候选池: BottomRight 可连 {BottomLeft, TopRight}",
          br.candidates() == ["HorizontalWallBottomLeft", "HorizontalWallTopRight"],
          str(br.candidates()))
    try:
        register_rule("HorizontalWallTopLeft", "HorizontalWallTopRight")
        check("重复注册同一类型对应拦截", False)
    except ValueError as e:
        check("重复注册同一类型对应拦截", "already registered" in str(e))

    print("\n== 2. 规则表初筛：未注册类型对(如 door 的角)直接驳回 ==")
    class DoorTopLeftPort(HorizontalWallTopLeftPort):
        port_type = "DoorTopLeft"
    class DoorTopRightPort(HorizontalWallTopRightPort):
        port_type = "DoorTopRight"
    class DoorBottomLeftPort(HorizontalWallBottomLeftPort):
        port_type = "DoorBottomLeft"
    class DoorBottomRightPort(HorizontalWallBottomRightPort):
        port_type = "DoorBottomRight"
    class DoorWall(HorizontalWall):
        structure_type = "door"
        def port_node_for_cell(self, cell):
            gw, gh = self._grid_width, self._grid_height
            return {
                (0, 0): DoorTopLeftPort,
                (gw - 1, 0): DoorTopRightPort,
                (0, gh - 1): DoorBottomLeftPort,
                (gw - 1, gh - 1): DoorBottomRightPort,
            }.get(cell)
    dw = DoorWall((4, 3), (64, 64), center=(900, 300))
    try:
        connect(a0, dw, side_a="right", side_b="left")
        check("未注册角类型对应驳回", False)
    except RuleNotFoundError:
        check("未注册角类型对应驳回", True)

    print("\n== 3. 只认 Port：缩放不同要靠 loose 几何（exact 现在会校验格尺寸） ==")
    a2 = HorizontalWall((4, 3), (64, 64), center=(200, 300))
    b2 = HorizontalWall((4, 3), (32, 32), center=(700, 300))   # 缩放不同
    r = connect(a2, b2, side_a="right", side_b="left", geometry="loose")
    check("缩放不同按 loose 连上", r.ok and len(r.connected) == 2, r.summary())

    a3 = HorizontalWall((4, 3), (64, 64), center=(200, 600))
    b3 = HorizontalWall((4, 5), (64, 64), center=(700, 600))    # 高不同(轮廓不等长)
    r = connect(a3, b3, side_a="right", side_b="left")
    check("高不同也能按 Port 连上(不再被轮廓拦截)", r.ok and len(r.connected) >= 1,
          r.summary())

    print("\n== 5. 联动连接：右↔左贴靠，两对对角 Port 批量连接（调用方移动适配） ==")
    obs.clear()
    A = ActionSpyWall((4, 3), (64, 64), center=(200, 300))
    B = ActionSpyWall((4, 3), (64, 64), center=(700, 300))
    a_before = A.shape.center
    b_before = B.shape.center
    result = connect(A, B, side_a="right", side_b="left")
    check("连接成功", result.ok)
    check("批量连接 2 对角 Port 对", len(result.connected) == 2, str(len(result.connected)))
    all_locked = all(pa.is_locked and pb.is_locked for pa, pb, _ in result.connected)
    check("所有配对 Port 已满/锁定(max=1)", all_locked)
    check("调用方(A=主动)移动适配", A.shape.center != a_before,
          f"{a_before} -> {A.shape.center}")
    check("对端(B=被动)保持原位", B.shape.center == b_before,
          f"{b_before} -> {B.shape.center}")
    a_left = A.shape.centerx - (4 * 64) / 2
    b_left = B.shape.centerx - (4 * 64) / 2
    check("A 右缘 == B 左缘（像素贴合）", abs(a_left + 4 * 64 - b_left) < 1e-6,
          f"A.right={a_left + 4*64}, B.left={b_left}")
    check("双方互记实例 ID(谁连了谁)", B in A.connections and A in B.connections)
    check("两端都收到连接回调",
          A.received_actions and all(a == "connect" for a in A.received_actions)
          and B.received_actions and all(a == "connect" for a in B.received_actions),
          f"A={A.received_actions} B={B.received_actions}")
    check("on_connect 回调记录了观测", len(obs) == 2, str(obs))

    print("\n== 6. 已锁定 Port 拒绝重复连接 ==")
    try:
        connect(A, B, side_a="right", side_b="left")
        check("重复连接被拦截(端口占用)", False)
    except PortBusyError as e:
        check("重复连接被拦截(端口占用)", "busy" in str(e).lower())

    print("\n== 7. 竖着叠放(bottom↔top) 复用按角注册规则 ==")
    C = ActionSpyWall((4, 3), (64, 64), center=(300, 900))
    D = ActionSpyWall((4, 3), (64, 64), center=(300, 300))
    r2 = connect(C, D, side_a="bottom", side_b="top")   # C 叠在 D 上方
    check("竖贴靠成功且复用同套规则", r2.ok and len(r2.connected) == 2, r2.summary())
    # C 在上方，需向下移动使 C.下缘 == D.上缘
    c_bottom = C.shape.centery + (3 * 64) / 2
    d_top = D.shape.centery - (3 * 64) / 2
    check("C.下缘 == D.上缘（像素贴合）", abs(c_bottom - d_top) < 1e-6,
          f"C.bottom={c_bottom}, D.top={d_top}")

    print("\n== 8. 显式隔离域（自建 RuleRegistry）不污染全局表 ==")
    from src.core.build_game.port import RuleRegistry, ConnectRule
    local = RuleRegistry()
    local.register(ConnectRule("Xa", "Xb", action="dock_x"))
    check("全局表不受隔离域影响", RULES.get("Xa", "Xb") is None)
    check("隔离域内可查询", local.get("Xa", "Xb") is not None)

    print("\n== 9. 竖墙贴横墙边（端口级连接）：竖墙头格 ↔ 横墙顶行角 ==")
    # 连接单元 = 端口：显式指定「竖墙头部端口(V.left[0]) ↔ 横墙右上角(H.right[0])」。
    # 调用方(竖墙 V)用其端口去连横墙 H 端口时，V 自身移动去适配 H（H 静止），
    # 且 V 若已连其它结构会级联带动一起平移（先 V 自身再其它）。
    from src.core.build_game.verticalwall import VerticalWall
    H = ActionSpyWall((5, 3), (64, 64), center=(600, 300))      # 横墙(对端/静止)
    V = VerticalWall(2, 5, center=(1000, 300))                  # 竖墙(调用方/移动)
    v_before = V.shape.center
    h_before = H.shape.center
    pa = V.get_side_ports("left")[0]      # 竖墙头格(0,0)=TopLeft(调用方=self)
    pb = H.get_side_ports("right")[0]     # 横墙右上角=TopRight(对端=peer)
    r3 = pa.connect_to(pb, self_side="left", peer_side="right")
    check("端口级连接成功", r3.ok and len(r3.connected) == 1, r3.summary())
    check("两端端口已满/锁定(max=1)", pa.is_locked and pb.is_locked)
    check("竖墙网格 = 1 × (长+高-1)", V.shape.size == (1, 6), str(V.shape.size))
    check("竖墙结构类型可观测", V.structure_type == "vertical_wall")
    check("连接的是竖墙头部(左) ↔ 横墙右上(右)",
          pa.edge_type("left") == "HorizontalWallTopLeft" and pb.edge_type("right") == "HorizontalWallTopRight",
          f"{pa.edge_type('left')} ↔ {pb.edge_type('right')}")
    # 调用方(竖墙 V)移动去贴对端(横墙 H)，H 静止：
    v_left = V.shape.centerx - (V.shape.size[0] * 64) / 2
    h_right = H.shape.centerx + (5 * 64) / 2
    check("调用方(V)移动适配，V.左缘贴合 H.右缘", abs(v_left - h_right) < 1e-6,
          f"V.left={v_left}, H.right={h_right}")
    check("调用方(竖墙)已移动", V.shape.center != v_before)
    check("对端(横墙)保持原位", H.shape.center == h_before)

    print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
