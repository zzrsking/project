"""
wall_drag_tool.py —— editor 临时调试工具（F 按住启用）

功能（2026 调试用途）：
- 按住 F 启用工具，松开 F 停用（若正在拖拽则立即取消，不生成墙体）；
- 按住鼠标左键：以按下点为起点开始拖拽，拖动时显示“白色半透明吸附框”；
- 吸附规则（类似 Excel 的网格磁吸）：
  * 起点/终点都吸附到整数网格（CELL=64px 的整数倍）；
  * 宽高分别独立吸附到最近的整格数（不再强制主方向），边缘平滑无抖动；
- 过渡动画：白框位置/尺寸用插值逐步逼近目标（“丝滑”感）；
- 松开左键（且 F 仍按住）：按吸附后的整格数生成一面横向木墙(HorizontalWoodWall)并放入世界；
- 撤销：提供 undo() 方法，可撤销最近一次生成的墙体（需绑定快捷键或外部调用）。
"""
import pygame

from src.core.build_game.horizontalwall import HorizontalWoodWall
from src.utils.layer_v2 import LayerSpec, LayerKind


CELL = 64            # 网格吸附粒度（与墙体 64px 一致）
MAX_GRID = 40        # 拖拽上限（防止误操作拖出巨大墙）
LERP = 0.35          # 平滑插值系数


def _snap(v: float) -> int:
    """吸附到最近整数格（以格为单位返回格数，带符号）。"""
    return int(round(v / CELL))


def _snap_pos(v: float) -> int:
    """把世界坐标吸附到网格线（格边界整数乘 CELL）。"""
    return int(round(v / CELL)) * CELL


class WallDragTool:
    def __init__(self, world=None):
        self.world = world
        self.enabled = False          # 工具是否激活（F 按下状态）
        self.dragging = False
        self.start_world = (0, 0)
        self.end_world = (0, 0)
        self.snap_rect = pygame.Rect(0, 0, 0, 0)      # 目标吸附矩形
        self.draw_rect = pygame.Rect(0, 0, 0, 0)      # 实际绘制矩形（插值中）

        # --- 撤销功能 ---
        self.undo_list = []           # 存储 (layer_name, wall_obj) 元组

    # ---------------- 事件处理 ----------------
    def handle_event(self, event, camera):
        """
        处理与工具相关的 pygame 事件。
        返回 True 表示事件已被消费，False 表示未处理。
        """
        if event.type == pygame.KEYDOWN and event.key == pygame.K_f:
            self.enabled = True
            return True

        if event.type == pygame.KEYUP and event.key == pygame.K_f:
            # F 键松开 → 停用工具；若正在拖拽则取消（不生成）
            self.enabled = False
            if self.dragging:
                self._reset()
            return True

        if not self.enabled:
            return False

        # 鼠标事件（仅在启用时处理）
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.dragging = True
            self.start_world = camera.screen_to_world(pygame.mouse.get_pos())
            self.end_world = self.start_world
            self._update_snap()
            return True

        if event.type == pygame.MOUSEMOTION and self.dragging:
            self.end_world = camera.screen_to_world(pygame.mouse.get_pos())
            self._update_snap()
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging:
            self.dragging = False
            # 只有在 F 仍按住的情况下才生成墙体，否则放弃
            if self.enabled:
                self._create_wall()
            self._reset()
            return True

        return False

    def _reset(self):
        """重置拖拽状态，清理预览矩形。"""
        self.dragging = False
        self.snap_rect = pygame.Rect(0, 0, 0, 0)
        self.draw_rect = pygame.Rect(0, 0, 0, 0)

    # ---------------- 吸附计算 ----------------
    def _update_snap(self):
        sx, sy = self.start_world
        ex, ey = self.end_world
        dx = ex - sx
        dy = ey - sy

        # 两方向独立吸附到整格（带符号）
        gx = _snap(dx)
        gy = _snap(dy)

        cols = max(2, min(MAX_GRID, abs(gx)))
        rows = max(2, min(MAX_GRID, abs(gy)))

        w = cols * CELL
        h = rows * CELL

        ox = _snap_pos(sx)
        oy = _snap_pos(sy)

        left = ox if dx >= 0 else ox - w
        top  = oy if dy >= 0 else oy - h

        target = pygame.Rect(left, top, w, h)
        if self.draw_rect.width == 0:
            self.draw_rect = target.copy()
        self.snap_rect = target

    # ---------------- 每帧动画 ----------------
    def update(self):
        if not self.enabled or not self.dragging:
            return

        r = self.draw_rect
        t = self.snap_rect
        self.draw_rect = pygame.Rect(
            int(r.x + (t.x - r.x) * LERP),
            int(r.y + (t.y - r.y) * LERP),
            int(r.w + (t.w - r.w) * LERP),
            int(r.h + (t.h - r.h) * LERP),
        )

    # ---------------- 绘制 ----------------
    def draw(self, surface, camera):
        if not self.enabled or self.draw_rect.width == 0:
            return
        p1 = camera.world_to_screen((self.draw_rect.left, self.draw_rect.top))
        p2 = camera.world_to_screen((self.draw_rect.right, self.draw_rect.bottom))
        sx = pygame.Rect(
            int(p1[0]), int(p1[1]),
            int(p2[0] - p1[0]), int(p2[1] - p1[1])
        )

        fill = pygame.Surface((max(1, sx.w), max(1, sx.h)), pygame.SRCALPHA)
        fill.fill((255, 255, 255, 60))
        surface.blit(fill, sx.topleft)
        pygame.draw.rect(surface, (255, 255, 255, 255), sx, 3)

    # ---------------- 生成墙体 ----------------
    def _create_wall(self):
        if self.snap_rect.width == 0:
            return
        w_cells = max(2, self.snap_rect.width // CELL)
        h_cells = max(2, self.snap_rect.height // CELL)
        center = self.snap_rect.center

        try:
            wall = HorizontalWoodWall((w_cells, h_cells), center)
        except Exception:
            from src.core.build_game.horizontalwall import HorizontalWall
            wall = HorizontalWall((w_cells, h_cells), (CELL, CELL), center)


        # 放入世界：走 add_game_object（会按需建/进 test 层 + 注册空间网格 +
        # 递增 geom_revision 触发渲染器重建对象缓存，保证墙体立刻可见）
        if self.world is not None:
            self.world.add_game_object(wall, "test")
            from src.editor.undo import next_seq
            self.undo_list.append((next_seq(), wall))   # (全局序号, 对象)
            print(f"[wall_drag] 已生成横墙 {w_cells}x{h_cells} @ {center}")

    # ---------------- 撤销 ----------------
    @property
    def last_seq(self):
        """栈顶可撤销项的全局序号（0 = 没有可撤销项）。供编辑器统一撤销排序用。"""
        if not self.undo_list:
            return 0
        entry = self.undo_list[-1]
        return int(entry[0]) if isinstance(entry, tuple) else int(getattr(entry, "seq", 0))

    def undo(self):
        """
        撤销最近一次生成的墙体。
        返回 True 表示撤销成功，False 表示无操作可撤销。
        """
        if not self.undo_list:
            print("[wall_drag] 没有可撤销的操作")
            return False

        entry = self.undo_list.pop()
        wall = entry[1] if isinstance(entry, tuple) else entry
        try:
            self.world.remove_game_object(wall)   # 注销空间网格/层并触发缓存重建
            print(f"[wall_drag] 已撤销墙体 @ {wall.center}")
            return True
        except (KeyError, AttributeError, ValueError) as e:
            print(f"[wall_drag] 撤销失败: {e}")
            # 虽然失败，但已弹出，为保持一致性，不再放回
            return False

    def toggle(self):
        self.enabled = True
        self._reset()
        return self.enabled

    def untoggle(self):
        self.enabled = False
        # self._reset()
        return self.enabled