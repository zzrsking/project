import pygame


class Block:
    """视口区块切分器。

    职责只有两件纯计算，不持有任何对象：
      1. 视口(世界矩形) → 覆盖到的区块坐标（`update_viewport`）；
      2. 任意世界矩形 → 覆盖到的区块坐标（`cells_of_rect`，空间网格挂载用）。

    性能约定：
      - `update_viewport` 返回"覆盖的区块是否变了"。调用方（GameWorld）据此在
        视口没跨区块时整帧跳过活跃集合重算，静止时接近零开销。
      - 区块矩形列表只在坐标真正变化时重建，不再每帧重新分配。
    """

    def __init__(self, block_size):
        """
        :param block_size: (width, height) 每个区块的像素尺寸
        """
        self.block_size = block_size
        self.active_blocks = []          # 世界坐标系下的区块矩形列表
        self.active_block_coords = []    # 对应的 (col, row) 列表
        # 视口外扩 1 圈的区块坐标：给"锁定候选"这类邻域查询用（不是渲染可见性）
        self.margin_block_coords = []
        self.margin_coords_set = frozenset()

        self.coords_key = None           # 上次视口对应的 (col0, col1, row0, row1)
        self.coords_set = frozenset()

    # ------------------------------------------------------------------
    # 纯几何
    # ------------------------------------------------------------------
    def _cell_range(self, left, top, right, bottom):
        """世界矩形覆盖的 (col0, col1, row0, row1)，闭区间。"""
        bw, bh = self.block_size
        left, top, right, bottom = int(left), int(top), int(right), int(bottom)
        return (left // bw, (right - 1) // bw, top // bh, (bottom - 1) // bh)

    def cells_of_rect(self, left, top, right, bottom):
        """世界矩形覆盖的区块坐标元组（空间网格挂载/查询用）。

        用元组而不是生成器：调用方要拿它跟上一帧的缓存做等价比较。
        """
        col0, col1, row0, row1 = self._cell_range(left, top, right, bottom)
        return tuple((col, row)
                     for col in range(col0, col1 + 1)
                     for row in range(row0, row1 + 1))

    def cells_of_object(self, obj):
        """对象占用的区块坐标（按 world_position 的包围盒）。"""
        wp = obj.world_position
        return self.cells_of_rect(wp.x, wp.y, wp.x + wp.width, wp.y + wp.height)

    def cell_at(self, x, y):
        """世界坐标点落在哪个区块。

        点查询必须用这个，不能拿 cells_of_rect(x, y, x, y)：那个函数的列区间是
        [x//bw, (x-1)//bw]，当 x 正好落在区块边界上时两端会算出不同的列，区间反而为空。
        """
        bw, bh = self.block_size
        return (int(x) // bw, int(y) // bh)

    # ------------------------------------------------------------------
    # 视口更新
    # ------------------------------------------------------------------
    def update_viewport(self, viewport_rect):
        """根据当前视口矩形（世界坐标）更新激活区块列表。

        viewport_rect: pygame.Rect 对象，表示屏幕在世界坐标系中的矩形区域。
        :return: 覆盖的区块坐标是否发生变化（False = 与上次完全相同，可整帧跳过）
        """
        if viewport_rect.width <= 0 or viewport_rect.height <= 0:
            if not self.active_block_coords:
                return False
            self.active_blocks = []
            self.active_block_coords = []
            self.margin_block_coords = []
            self.margin_coords_set = frozenset()
            self.coords_set = frozenset()
            self.coords_key = ()
            return True

        key = self._cell_range(viewport_rect.left, viewport_rect.top,
                               viewport_rect.right, viewport_rect.bottom)
        if key == self.coords_key:
            return False

        col0, col1, row0, row1 = key
        block_w, block_h = self.block_size

        coords = []
        blocks = []
        for col in range(col0, col1 + 1):
            x = col * block_w
            for row in range(row0, row1 + 1):
                y = row * block_h
                coords.append((col, row))
                blocks.append(pygame.Rect(x, y, block_w, block_h))
        self.active_blocks = blocks
        self.active_block_coords = coords
        self.coords_set = frozenset(coords)

        # 外扩 1 圈：锁定距离(默认 300px) < 区块尺寸(默认 400px)，一圈足够覆盖
        margin = []
        for col in range(col0 - 1, col1 + 2):
            for row in range(row0 - 1, row1 + 2):
                margin.append((col, row))
        self.margin_block_coords = margin
        self.margin_coords_set = frozenset(margin)

        self.coords_key = key
        return True
