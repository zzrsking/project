"""
rectangle_house.py —— 矩形房子组装（RectangleHouse）

一个矩形房子由三部分组成，按“长 / 宽 / 高”参数化映射到墙体/地板并端口连接：
- 地板   : Brick/Floor         网格 = 长度 × 宽度（底面）
- 顶/底墙 : HorizontalWall      跨度 = 长度（前后面），高度 = height（行数）
- 左/右墙 : VerticalWall        长度 = 宽度+1（侧面水平延伸），高度 = height（行数）

连接（端口相接，几何由 port.connect_to 的吸附自动对齐）：
  floor.top_left    ↔ top_wall.bottom_left
  floor.top_right   ↔ top_wall.bottom_right
  floor.bottom_left ↔ bottom_wall.bottom_left  +  left_wall.bottom   (地板下角口 max=2)
  floor.bottom_right↔ bottom_wall.bottom_right +  right_wall.bottom  (地板下角口 max=2)
  其中「地板下角 ↔ 底墙下角」为**重叠**连接：两侧统一走 bottom↔bottom，
  底墙下角端口(Overlap)与地板下角端口(FloorCornerBottom)完全叠合（格心重合），
  使底墙的底排落在门厅地板底缘之上（“腿部”重叠）。

使用（两种材料）：
  house = RectangleHouse(length=6, width=4, height=3, center=(0,0), use_wood=True)
  for s in house.children:
      world.add_game_object(s, "house")

  # headless 骨架验证（不加载纹理实体）：use_wood=False
  house = RectangleHouse(6, 4, 3, use_wood=False)
"""

from src.core.build_game.structure import Structure, RenderOrder
from src.core.build_game.horizontalwall import HorizontalWall
from src.core.build_game.verticalwall import VerticalWall, register_wall_family_corner_rules
from src.core.build_game.floor import Floor, register_floor_corner_rules
from src.core.build_game.port import register_rule, RULES, ConnectError


class RectangleHouse:
    """矩形房子组装：封装一组子结构 + 端口连接，产出可直接入世界的 Structure 列表。

    并非单张精灵，而是多个 Structure 的集合（地板+四墙）。children 提供被渲染的
    子结构；is_single Structure 复合化待后续需要时再做（当前渲染模型按 leaf sprite
    逐精灵绘制，交由子结构各自入 world 最稳）。
    """

    def __init__(self, length: int, width: int, height: int,
                 cell: tuple[int | float, int | float] = (64, 64),
                 center: tuple[float, float] = (0, 0),
                 use_wood: bool = True):
        if length < 2 or width < 2 or height < 2:
            raise ValueError(
                f"尺寸需 >=2（引擎不允许 N×1 格）：length={length} width={width} height={height}")
        self.length = int(length)
        self.width = int(width)
        self.height = int(height)          # 墙的行数也由此决定
        self.cell = tuple(cell)
        self.center = tuple(center)
        self.use_wood = bool(use_wood)

        # 记录“谁连了谁”的观测
        self.log: list[str] = []
        # 子结构
        self.floor = None
        self.top_wall = None
        self.bottom_wall = None
        self.left_wall = None
        self.right_wall = None
        self.children: list = []

        self._build()
        self.connect_all()

    # ---- 构建组件 ----
    def _make_floor(self):
        if self.use_wood:
            from src.core.build_game.floor import BrickFloor
            return BrickFloor((self.length, self.width), self.center)
        return Floor((self.length, self.width), self.cell, self.center)

    def _make_horizontal_wall(self, offset: tuple[float, float], overlap_bottom: bool = False):
        if self.use_wood:
            from src.core.build_game.horizontalwall import HorizontalWoodWall
            cx = self.center[0] + offset[0]
            cy = self.center[1] + offset[1]
            return HorizontalWoodWall((self.length, self.height), (cx, cy),
                                      overlap_bottom=overlap_bottom)
        return HorizontalWall((self.length, self.height), self.cell,
                              (self.center[0] + offset[0], self.center[1] + offset[1]),
                              overlap_bottom=overlap_bottom)

    def _make_vertical_wall(self, offset: tuple[float, float]):
        # 侧墙（竖墙）：长度 = 底面宽(width) + 1 —— 中部(top_run)因此多 2 格；
        # 高度 = self.height（墙的行数）
        v_length = self.width + 1
        v_height = self.height
        if self.use_wood:
            from src.core.build_game.verticalwall import VerticalWoodWall
            return VerticalWoodWall(v_length, v_height,
                                    center=(self.center[0] + offset[0], self.center[1] + offset[1]))
        return VerticalWall(v_length, v_height, self.cell,
                            (self.center[0] + offset[0], self.center[1] + offset[1]))

    def _build(self):
        # 先铺地板（基准面），四周墙按大致位置放，后续 connect 吸附对齐
        self.floor = self._make_floor()
        self.top_wall = self._make_horizontal_wall((0, -(self.width * self.cell[1] / 2)))
        # 底墙：下两角挂「可重叠」端口（与地板下角走 bottom↔bottom 完全重叠）
        self.bottom_wall = self._make_horizontal_wall(
            (0, (self.width * self.cell[1] / 2)), overlap_bottom=True)
        self.left_wall = self._make_vertical_wall((-(self.length * self.cell[0] / 2), 0))
        self.right_wall = self._make_vertical_wall(((self.length * self.cell[0] / 2), 0))
        self.children = [self.floor, self.top_wall, self.bottom_wall,
                         self.left_wall, self.right_wall]

    # ---- 规则 / 连接 ----
    def _ensure_rules(self):
        """注册墙族四角规则 + 地板角与墙族互连规则（一份写一遍，重复注册安全跳过）。"""
        for r in register_wall_family_corner_rules(log=self.log):
            pass
        for r in register_floor_corner_rules(log=self.log):
            pass

    def _try_connect(self, a, b, self_side, peer_side, label):
        """尽力连接，成功/失败信息写入 log，不中断整组装。"""
        try:
            res = a.connect_to(b, self_side=self_side, peer_side=peer_side)
            self.log.append(f"[OK] {label}  {res.summary()}")
            return True
        except ConnectError as e:
            self.log.append(f"[REJECT] {label}  {type(e).__name__}: {e}")
            return False
        except Exception as e:
            self.log.append(f"[ERR] {label}  {type(e).__name__}: {e}")
            return False

    def connect_all(self):
        """按矩形房拓扑端口连接（地板角口 max=2，下角各接两个）。

        连接顺序（先横墙后竖墙）：
        ① 底墙 下左+下右 ↔ 地板 下左+下右（同列对齐，bottom↔bottom → 完全重叠）
        ② 顶墙 左下+右下 ↔ 地板 左上+右上
        ③ 竖墙.button ↔ 地板 下角（互补贴靠）
        ④ 竖墙.tail  ↔ 底墙 左上+右上
        ⑤ 竖墙.top   ↔ 顶墙 左上+右上
        """
        self._ensure_rules()
        f, ut, dt, vl, vr = (self.floor, self.top_wall, self.bottom_wall,
                             self.left_wall, self.right_wall)

        # ① 底墙 下角 ↔ 地板 下角（先连横墙：同列对齐，两侧统一走 bottom↔bottom → 完全重叠）。
        #  地板下角 max=2 可接两个，但两条路径必须走【不同的地板边】：
        #  竖墙将占 左/右 边，故底墙这路走地板下角的 bottom 边；底墙侧也走 bottom，
        #  使 side_a==side_b 触发 OverlapMixinPort 的“重叠”吸附，格心重合。
        #  因底墙与地板同宽，左↔左、右↔右 的对齐是自洽的（整排底格叠在地板下缘上）。
        self._try_connect(dt.port("bottom_left"), f.port("bottom_left"),
                          "bottom", "bottom", "bottom_wall.bottom_left ↔ floor.bottom_left (重叠)")
        self._try_connect(dt.port("bottom_right"), f.port("bottom_right"),
                          "bottom", "bottom", "bottom_wall.bottom_right ↔ floor.bottom_right (重叠)")

        # ② 顶墙 下角 ↔ 地板 上角
        self._try_connect(ut.port("bottom_left"), f.port("top_left"),
                          "bottom", "top", "top_wall.bottom_left ↔ floor.top_left")
        self._try_connect(ut.port("bottom_right"), f.port("top_right"),
                          "bottom", "top", "top_wall.bottom_right ↔ floor.top_right")

        # ③ 竖墙.button ↔ 地板下角（后连竖墙：互补贴靠）
        self._try_connect(vl.port("bottom"), f.port("bottom_left"),
                          "right", "left", "left_wall.bottom ↔ floor.bottom_left")
        self._try_connect(vr.port("bottom"), f.port("bottom_right"),
                          "left", "right", "right_wall.bottom ↔ floor.bottom_right")

        # ④ 竖墙.tail ↔ 底墙 上角
        self._try_connect(vl.port("tail"), dt.port("top_left"),
                          "right", "left", "left_wall.tail ↔ bottom_wall.top_left")
        self._try_connect(vr.port("tail"), dt.port("top_right"),
                          "left", "right", "right_wall.tail ↔ bottom_wall.top_right")

        # ⑤ 竖墙.top ↔ 顶墙 上角
        self._try_connect(vl.port("top"), ut.port("top_left"),
                          "right", "left", "left_wall.top ↔ top_wall.top_left")
        self._try_connect(vr.port("top"), ut.port("top_right"),
                          "left", "right", "right_wall.top ↔ top_wall.top_right")

    # ---- 观测 ----
    def describe(self) -> str:
        return (f"RectangleHouse(length={self.length}, width={self.width}, height={self.height}, "
                f"wood={self.use_wood})")

    def debug(self) -> str:
        """打印房子长/宽/高参数 + 各子结构尺寸/位置 + 连接观测，便于排查装配几何。"""
        def gsize(s):
            return (getattr(s, "_grid_width", 0), getattr(s, "_grid_height", 0))

        def topleft(s):
            return (s.shape.centerx - (s._grid_width * s._cell_w) / 2,
                    s.shape.centery - (s._grid_height * s._cell_h) / 2)

        lines = [
            f"RectangleHouse debug: length={self.length}(X) width={self.width}(Y) "
            f"height={self.height} cell={self.cell} use_wood={self.use_wood}",
            "  ---- 子结构 (grid尺寸 → center / top_left) ----",
        ]
        for name, s in [("floor", self.floor), ("top_wall", self.top_wall),
                        ("bottom_wall", self.bottom_wall),
                        ("left_wall", self.left_wall), ("right_wall", self.right_wall)]:
            if s is None:
                continue
            tl = topleft(s)
            lines.append(
                f"  {name:11s} grid={gsize(s)} shape={s.shape.size} "
                f"center=({s.shape.centerx:.0f},{s.shape.centery:.0f}) "
                f"top_left=({tl[0]:.0f},{tl[1]:.0f})")
        lines.append("  ---- 连接观测 ----")
        for line in self.log:
            lines.append(f"    {line}")
        return "\n".join(lines)

    def __repr__(self):
        return f"{type(self).__name__}({self.describe()})"