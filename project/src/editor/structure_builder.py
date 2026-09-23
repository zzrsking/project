"""结构生成器：面板里选好结构后，在世界里拖拽吸附并生成。

就是原有 WallDragTool 那套"拖拽 + 网格吸附 + 缓动预览"的推广版：
  · 吸附规则完全不变（起点/终点各自吸附到 64px 整格，宽高独立取整格数）；
  · 预览框位置/尺寸用插值逐步逼近目标，保证"丝滑"；
  · 区别只在于松手时按 kind 生成不同的结构（横墙 / 竖墙 / 房子 / 地板 / 木桌）。
"""
import pygame

from src.ui.elements import theme
from src.ui.elements.fonts import get_font

CELL = 64                 # 网格吸附粒度
MAX_GRID = 40             # 拖拽上限（防止误拖出巨大结构）
LERP = 0.35               # 预览框平滑插值系数
MIN_CELLS = 2             # 引擎不允许 N×1 格（墙类）
# 木桌没有这个限制：1×1 就是"一张单桌"，所以最小 1 格
MIN_CELLS_BY_KIND = {"table": 1}

HOUSE_DEFAULT_HEIGHT = 3  # 房子默认高度
VWALL_DEFAULT_HEIGHT = 3  # 竖墙默认高度（网格 = 1 格宽 × (长+高-1) 行）
ROW_SUB_STEPS = 4         # 面板滚动/悬停的亚档细分（缓存用）

# kind -> 显示名（提示用）
KIND_LABELS = {
    "wall_h": "横墙",
    "wall_v": "竖墙",
    "house": "房子",
    "hall": "走廊",
    "hall_ns": "竖向连廊",
    "hall_ew": "横向连廊",
    "floor": "地板",
    "table": "木桌",
}

# 走廊类 kind -> Hall 的朝向（hall 保留为竖向，兼容旧调用）
HALL_ORIENTATION = {"hall": "ns", "hall_ns": "ns", "hall_ew": "ew"}

HALL_DEFAULT_HEIGHT = 3   # 走廊默认墙高（与房子一致）

DEFAULT_LAYER = "test"    # 与既有的生成逻辑一致


def _snap(v):
    """世界坐标差 -> 整格数（带符号）。"""
    return int(round(v / CELL))


def _snap_pos(v):
    """世界坐标 -> 最近的网格线。"""
    return int(round(v / CELL)) * CELL


class StructureBuilder:
    def __init__(self, world=None):
        self.world = world
        self.kind = None              # 当前待生成的结构类型；None = 未激活
        self.dragging = False
        self.start_world = (0, 0)
        self.end_world = (0, 0)
        self.snap_rect = pygame.Rect(0, 0, 0, 0)
        self.draw_rect = pygame.Rect(0, 0, 0, 0)
        self.undo_list = []

    # ------------------------------------------------------------------ #
    # 激活 / 关闭
    # ------------------------------------------------------------------ #
    @property
    def active(self):
        return self.kind is not None

    def arm(self, kind):
        self.kind = kind
        self._reset()
        return self.kind

    def disarm(self):
        self.kind = None
        self._reset()

    def toggle(self, kind):
        if self.kind == kind:
            self.disarm()
        else:
            self.arm(kind)
        return self.kind

    def _reset(self):
        self.dragging = False
        self.snap_rect = pygame.Rect(0, 0, 0, 0)
        self.draw_rect = pygame.Rect(0, 0, 0, 0)

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def handle_event(self, event, camera):
        """返回 True 表示事件已被消费。"""
        if not self.active:
            return False

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.disarm()
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.dragging = True
            self.start_world = camera.screen_to_world(event.pos)
            self.end_world = self.start_world
            self._update_snap()
            return True

        if event.type == pygame.MOUSEMOTION and self.dragging:
            pos = event.pos if hasattr(event, "pos") else pygame.mouse.get_pos()
            self.end_world = camera.screen_to_world(pos)
            self._update_snap()
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging:
            self.dragging = False
            self._create()
            self._reset()
            return True

        return False

    # ------------------------------------------------------------------ #
    # 吸附
    # ------------------------------------------------------------------ #
    @staticmethod
    def vwall_split(rows):
        """竖墙的网格恒为 **1 格宽** × rows 行，且 rows = 长 + 高 - 1。

        拖拽只能给出行数，所以这里按默认高度 3 拆一下：
        高度 min(3, rows-1)，剩下的都算"长"。
        """
        rows = max(MIN_CELLS, int(rows))
        height = max(1, min(VWALL_DEFAULT_HEIGHT, rows - 1))
        length = rows - height + 1
        return length, height

    def _min_cells(self):
        """该结构的最小格数：墙类 2（引擎不允许 N×1），木桌 1（单张桌子就合法）。"""
        return MIN_CELLS_BY_KIND.get(self.kind, MIN_CELLS)

    def _update_snap(self):
        sx, sy = self.start_world
        ex, ey = self.end_world
        dx, dy = ex - sx, ey - sy
        lo = self._min_cells()

        if self.kind == "wall_v":
            # 竖墙只有一格宽：横向一律锁 1 格，拖拽只决定竖直方向的格数
            cols = 1
            rows = max(lo, min(MAX_GRID, abs(_snap(dy))))
            w, h = CELL, rows * CELL
            left = _snap_pos(sx)
            top = _snap_pos(sy) if dy >= 0 else _snap_pos(sy) - h
            target = pygame.Rect(left, top, w, h)
            if self.draw_rect.width == 0:
                self.draw_rect = target.copy()
            self.snap_rect = target
            return

        cols = max(lo, min(MAX_GRID, abs(_snap(dx))))
        rows = max(lo, min(MAX_GRID, abs(_snap(dy))))
        w, h = cols * CELL, rows * CELL

        ox, oy = _snap_pos(sx), _snap_pos(sy)
        left = ox if dx >= 0 else ox - w
        top = oy if dy >= 0 else oy - h

        target = pygame.Rect(left, top, w, h)
        if self.draw_rect.width == 0:
            self.draw_rect = target.copy()
        self.snap_rect = target

    def update(self):
        if not self.active or not self.dragging:
            return
        r, t = self.draw_rect, self.snap_rect
        self.draw_rect = pygame.Rect(
            int(r.x + (t.x - r.x) * LERP),
            int(r.y + (t.y - r.y) * LERP),
            int(r.w + (t.w - r.w) * LERP),
            int(r.h + (t.h - r.h) * LERP),
        )

    # ------------------------------------------------------------------ #
    # 绘制预览
    # ------------------------------------------------------------------ #
    def draw(self, surface, camera):
        if not self.active:
            return

        if self.draw_rect.width > 0:
            p1 = camera.world_to_screen((self.draw_rect.left, self.draw_rect.top))
            p2 = camera.world_to_screen((self.draw_rect.right, self.draw_rect.bottom))
            sx = pygame.Rect(int(p1[0]), int(p1[1]),
                             int(p2[0] - p1[0]), int(p2[1] - p1[1]))

            fill = pygame.Surface((max(1, sx.w), max(1, sx.h)), pygame.SRCALPHA)
            fill.fill((*theme.ACCENT_HI, 46))
            surface.blit(fill, sx.topleft)
            pygame.draw.rect(surface, theme.ACCENT_HI, sx, 2)
            # 网格线：让"吸附到哪一格"一眼可见
            for gx in range(CELL, max(CELL, sx.w), CELL):
                pygame.draw.line(surface, (*theme.ACCENT_HI, 60),
                                 (sx.x + gx, sx.y), (sx.x + gx, sx.bottom), 1)
            for gy in range(CELL, max(CELL, sx.h), CELL):
                pygame.draw.line(surface, (*theme.ACCENT_HI, 60),
                                 (sx.x, sx.y + gy), (sx.right, sx.y + gy), 1)

            label = self._label()
            font = get_font(14)
            text = font.render(label, True, theme.TEXT)
            pad = 6
            box = pygame.Rect(sx.x, sx.y - text.get_height() - pad * 2,
                              text.get_width() + pad * 2, text.get_height() + pad * 2)
            # 不透明底：半透明会让下面的世界透出来，看着像字"糊"在一起
            pygame.draw.rect(surface, theme.CARD_BG, box,
                             border_radius=theme.RADIUS_ROW)
            pygame.draw.rect(surface, theme.BORDER, box, 1,
                             border_radius=theme.RADIUS_ROW)
            surface.blit(text, (box.x + pad, box.y + pad))
        else:
            # 还没开始拖：在鼠标旁边提示一下当前待放置的结构
            font = get_font(14)
            text = font.render(
                f"拖拽放置：{KIND_LABELS.get(self.kind, self.kind)}", True,
                theme.TEXT)
            pos = pygame.mouse.get_pos()
            box = pygame.Rect(pos[0] + 14, pos[1] + 14,
                              text.get_width() + 12, text.get_height() + 8)
            pygame.draw.rect(surface, theme.CARD_BG, box,
                             border_radius=theme.RADIUS_ROW)
            pygame.draw.rect(surface, theme.BORDER, box, 1,
                             border_radius=theme.RADIUS_ROW)
            surface.blit(text, (box.x + 6, box.y + 4))

    def _label(self):
        """拖拽中标签：竖墙显示"长/高"，其余显示格数。"""
        lo = self._min_cells()
        cols = max(lo, self.draw_rect.width // CELL)
        rows = max(lo, self.draw_rect.height // CELL)
        name = KIND_LABELS.get(self.kind, self.kind)
        if self.kind == "wall_v":
            length, height = self.vwall_split(rows)
            return f"{name}  1 格宽 · 长 {length} · 高 {height}"
        if self.kind == "house":
            return f"{name}  {cols}×{rows}  高 {HOUSE_DEFAULT_HEIGHT}"
        return f"{name}  {cols}×{rows}"

    # ------------------------------------------------------------------ #
    # 生成
    # ------------------------------------------------------------------ #
    def _create(self):
        if self.snap_rect.width == 0 or self.world is None:
            return None

        cols = max(self._min_cells(), self.snap_rect.width // CELL)
        rows = max(self._min_cells(), self.snap_rect.height // CELL)
        center = self.snap_rect.center
        kind = self.kind

        try:
            obj = self._make(kind, cols, rows, center)
        except Exception as e:            # 生成失败不该把编辑器带崩
            print(f"[structure] 生成 {kind} 失败: {e}")
            return None

        if obj is None:
            return None

        self.world.add_game_object(obj, DEFAULT_LAYER)
        from src.editor.undo import next_seq
        self.undo_list.append((next_seq(), obj))     # (全局序号, 对象) —— 供统一撤销排序
        print(f"[structure] 已生成 {KIND_LABELS.get(kind, kind)} "
              f"{cols}x{rows} @ {center}")
        # 生成完自动卸下，避免手一抖又放一个
        self.kind = None
        return obj

    @staticmethod
    def _make(kind, cols, rows, center):
        """按 kind 生成结构。cols/rows 都是"拖拽出来的格数"。

        竖墙的宽恒为 1 格，所以它不直接用 cols，而是把 rows 拆成长/高 ——
        拆法就放在这里，保证"预览标签"和"真正生成"用的是同一套规则。
        """
        if kind == "wall_h":
            from src.core.build_game.horizontalwall import HorizontalWoodWall
            return HorizontalWoodWall((cols, rows), center)

        if kind == "wall_v":
            # 竖墙语义 = (长, 高)：网格 1×(长+高-1)
            from src.core.build_game.verticalwall import VerticalWoodWall
            length, height = StructureBuilder.vwall_split(rows)
            return VerticalWoodWall(length, height, center=center)

        if kind == "house":
            from src.core.build_game.house import House
            return House.build(max(MIN_CELLS, cols), max(MIN_CELLS, rows),
                               HOUSE_DEFAULT_HEIGHT, center=center,
                               use_wood=True)

        if kind in HALL_ORIENTATION:
            # 走廊：地板 + 一个轴向上的两道墙，另一个轴向两端开口（见 hall.py）
            from src.core.build_game.hall import Hall
            return Hall.build(max(MIN_CELLS, cols), max(MIN_CELLS, rows),
                              HALL_DEFAULT_HEIGHT, center=center, use_wood=True,
                              orientation=HALL_ORIENTATION[kind])

        if kind == "floor":
            from src.core.build_game.floor import (BrickFloor,
                                                   register_floor_corner_rules)
            register_floor_corner_rules()          # 幂等
            return BrickFloor((cols, rows), center)

        if kind == "table":
            from src.core.build_game.on_the_table import (WoodTable,
                                                          register_table_rules)
            register_table_rules()                 # 幂等
            wood_table = WoodTable((max(1, cols), max(1, rows)), center)
            from src.game.entity import TablePlantTile
            plant = TablePlantTile()
            wood_table.place_object((0, 0), plant)
            return wood_table

        return None

    # ------------------------------------------------------------------ #
    # 撤销
    # ------------------------------------------------------------------ #
    @property
    def last_seq(self):
        """栈顶可撤销项的全局序号（0 = 没有可撤销项）。供编辑器统一撤销排序用。"""
        if not self.undo_list:
            return 0
        entry = self.undo_list[-1]
        return int(entry[0]) if isinstance(entry, tuple) else int(getattr(entry, "seq", 0))

    def undo(self):
        """撤销最近一次生成的结构。返回是否撤销成功。"""
        if not self.undo_list:
            print("[structure] 没有可撤销的操作")
            return False
        entry = self.undo_list.pop()
        obj = entry[1] if isinstance(entry, tuple) else entry
        try:
            self.world.remove_game_object(obj)
            print(f"[structure] 已撤销最近生成的结构 {type(obj).__name__}")
            return True
        except (KeyError, AttributeError, ValueError) as e:
            print(f"[structure] 撤销失败: {e}")
            return False
