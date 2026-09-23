"""tiledshape.py —— 矩形/网格几何描述（Structure 的地基）。

只提供被真正用到的东西：`RectDock`（端口几何）、`FloatRect`、`RectSpace`、`TiledRect`。
历史上这里还有一组"贴靠几何验证"的常量与函数（`SIDES` / `OPPOSITE_SIDE` /
`DEFAULT_SIDE_PAIRS` / `is_opposite_pair` / `dock_offset_px`），
但吸附偏移早已改由 `Port._snap_self_to_peer` 直接算，它们没有任何调用者，已删除。
"""
import pygame


class RectDock:
    """
    矩形网格的像素几何描述 —— Structure 与 tiledshape 之间的“形状几何契约”。

    - grid_w / grid_h : 逻辑网格尺寸（格数）
    - cell_w / cell_h : 每格像素尺寸（缩放因子；连接要求两端相同）
    - cx / cy         : 结构像素中心

    该对象只回答纯几何问题：贴靠偏移量、侧边长度、边界格序列。
    复杂形状（L 形 / 凸形）将来通过扩展本类或替换本对象实现，
    port 的连接引擎无需感知具体形状。
    """
    def __init__(self, grid_w, grid_h, cell_w, cell_h, cx=0.0, cy=0.0):
        self.grid_w = int(grid_w)
        self.grid_h = int(grid_h)
        self.cell_w = float(cell_w)
        self.cell_h = float(cell_h)
        self.cx = float(cx)
        self.cy = float(cy)

    # ---- 基本像素量 ----
    @property
    def width(self):
        return self.grid_w * self.cell_w

    @property
    def height(self):
        return self.grid_h * self.cell_h

    @property
    def center(self):
        return (self.cx, self.cy)

    @property
    def top_left(self):
        return (self.cx - self.width / 2.0, self.cy - self.height / 2.0)

    @property
    def scale(self):
        """缩放因子（必须相等才允许直接连接；不等则触发适配器事件）。"""
        return (self.cell_w, self.cell_h)

    def side_length(self, side: str) -> int:
        """某条边上的边界单元数（沿边的格数）。"""
        if side in ("top", "bottom"):
            return self.grid_w
        if side in ("left", "right"):
            return self.grid_h
        raise ValueError(f"unknown side {side!r}")

    def boundary_cells(self, side: str) -> list:
        """
        某条边的“边界格”列表，顺序为沿边方向（top/bottom 从左到右；left/right 从上到下）。
        目前矩形全实心：每侧恰为一条完整的边。
        """
        if side == "top":
            return [(gx, 0) for gx in range(self.grid_w)]
        if side == "bottom":
            return [(gx, self.grid_h - 1) for gx in range(self.grid_w)]
        if side == "left":
            return [(0, gy) for gy in range(self.grid_h)]
        if side == "right":
            return [(self.grid_w - 1, gy) for gy in range(self.grid_h)]
        raise ValueError(f"unknown side {side!r}")

    def edge_profile(self, side: str) -> str:
        """
        接触面“边缘轮廓签名”：沿该边逐格编码，'X'=该格存在边界、'-'=该格缺失(凹口)。
        用于“形状不兼容”分层拦截（凸凹不咬合时签名不同，直接驳回）。
        """
        return "X" * self.side_length(side)


# =====================================

# ========== FloatRect ==========
class FloatRect:
    """支持浮点数的矩形容器，提供与 pygame.Rect 相似的属性接口。"""
    __slots__ = ('x', 'y', 'w', 'h')

    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = float(x)
        self.y = float(y)
        self.w = float(w)
        self.h = float(h)

    @property
    def left(self):
        return self.x
    @property
    def top(self):
        return self.y
    @property
    def right(self):
        return self.x + self.w
    @property
    def bottom(self):
        return self.y + self.h

    @property
    def centerx(self):
        return self.x + self.w / 2.0
    @property
    def centery(self):
        return self.y + self.h / 2.0

    @property
    def center(self):
        return (self.centerx, self.centery)
    @center.setter
    def center(self, value):
        cx, cy = value
        self.x = cx - self.w / 2.0
        self.y = cy - self.h / 2.0

    @property
    def topleft(self):
        return (self.left, self.top)
    @property
    def bottomright(self):
        return (self.right, self.bottom)

    @property
    def size(self):
        return (self.w, self.h)

    def move(self, dx: float, dy: float):
        self.x += dx
        self.y += dy

    def move_ip(self, dx: float, dy: float):
        self.x += dx
        self.y += dy

# =====================================

class RectSpace:
    def __init__(self, size: tuple[int, int]):
        self._size = size
        self._container = {}

    def _position_is_valid(self, position: tuple[int, int]):
        if len(position) == 2:
            if type(position[0]) is int and type(position[1]) is int:
                if 0 <= position[0] < self._size[0] and 0 <= position[1] < self._size[1]:
                    return True
                else:
                    raise ValueError("Position out of bounds")
            else:
                raise TypeError("position must be int")
        else:
            raise ValueError(f"position {position} is not supported")

    def get_size(self) -> tuple[int, int]:
        return self._size

    def register(self, sprite: pygame.sprite.Sprite, position: tuple[int, int], overwrite: bool = False):
        """注册精灵到指定格点。
        :param overwrite: 若为True，允许覆盖已有精灵；否则位置被占用时抛出ValueError。
        """
        if self._position_is_valid(position):
            if position in self._container and not overwrite:
                raise ValueError(f"Position {position} already occupied. Use overwrite=True to replace.")
            self._container[position] = sprite

    def remove(self, position: tuple[int, int]):
        if self._position_is_valid(position):
            self._container.pop(position, None)

    def get(self, position: tuple[int, int]) -> pygame.sprite.Sprite | None:
        if self._position_is_valid(position):
            return self._container.get(position, None)
        return None

    def get_all(self) -> list[pygame.sprite.Sprite]:
        return list(self._container.values())

    def __iter__(self):
        """迭代返回 (position, sprite) 元组"""
        return iter(self._container.items())


class TiledRect(FloatRect):
    def __init__(self, size: tuple[int, int], center: tuple[float | int, float | int] = (0, 0),
                 allow_1xN: bool = False):
        # 尺寸校验：正数、整数；默认不允许 1×N / N×1 / 1×1
        if size[0] <= 0 or size[1] <= 0:
            raise ValueError("width and height must be positive integers")
        if type(size[0]) is not int or type(size[1]) is not int:
            raise ValueError("size must be int")
        if size[0] == 1 or size[1] == 1:
            # allow_1xN=True 时允许"某一个维度为 1"的【条状】形状：
            #   · 1×N —— 一列多行（竖墙恒宽 1；横墙被切成一列宽的补墙段）
            #   · N×1 —— 一行多列（横墙被切成一行高的补墙段）
            #   · 1×1 —— 单格（段语义与端口都退化成单格，由调用方自行负责）
            # 原来只放行 1×N，N×1 与 1×1 一律抛错 —— 于是"只剩一行"的补墙段
            # 根本造不出来，只能靠"克隆整面墙再挖格"绕过，挖完端口也就没了。
            if not allow_1xN:
                raise ValueError(
                    "size of 1 in a dimension is not supported "
                    "(1×1 / 1×N / N×1 不允许；可用 allow_1xN=True 建条状形状)")

        super().__init__(0, 0, float(size[0]), float(size[1]))
        self.center = center   # 调用 FloatRect 的 center setter，浮点不截断
        self._space = RectSpace(size)

    def get_space(self) -> RectSpace:
        return self._space

    def get_all_sprites(self) -> list[pygame.sprite.Sprite]:
        return self._space.get_all()


class VerticalTiledRect(TiledRect):
    """
    专为竖墙设计的纵向形状（原来 TiledRect 是横墙语义的通用矩形）。
    - 只允许 1 列 × N 行（宽恒 1，N>=2）
    - 列方向：一列（宽=1）从上往下铺；行方向：纵向叠层
    - 禁止 1×1 与 N×1 —— 与“宽恒 1、至少两层”的竖墙约定一致
    """
    def __init__(self, length: int, height: int,
                 center: tuple[float | int, float | int] = (0, 0)):
        # 长(length)=沿地面向下的列数语义并入行数。
        # 总行数 = length + height - 1：前 length 行为顶段(head/top_run/top_tail)，
        # 后 height-1 行为侧段(middle+bottom)。
        rows = int(length) + int(height) - 1
        if rows < 2:
            raise ValueError("vertical wall must be at least 2 rows (长+高-1 >= 2)")
        TiledRect.__init__(self, (1, rows), center, allow_1xN=True)