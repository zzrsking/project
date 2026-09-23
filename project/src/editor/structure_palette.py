"""结构编辑面板：F 键在鼠标旁呼出的"调色盘"。

交互
----
  · F           开 / 关（在鼠标位置弹出）
  · 滚轮        滚动列表
  · 左键        选择某个结构（选完自动关闭，并让世界那边进入拖拽生成）
  · Esc / 点外面  关闭

设计（和 button / scrollbar 同一套思路）
--------------------------------------
  · 列表表面按 (开合档, 滚动档, 悬停行, 选中项) 量化缓存 —— 内容没变就只 blit，
    所以静止时开销接近零；弹窗本身也只有 200 多像素宽。
  · 开合是 easeOutBack 的缩放+淡入，滚动和悬停都走缓动，不做硬切换。
  · 配色全部取自 theme（= 游戏 UI 那套），和编辑器其余部分一致。

只依赖 pygame + theme/fonts，不反向 import editor，便于单独测试。
"""
import math
import pygame

from src.ui.elements import theme
from src.ui.elements.fonts import get_font


# ---- 尺寸 ----
WIDTH = 236
HEADER_H = 38
ROW_H = 40               # 行高要放得下"名称(15号 19px) + 说明(11号 14px)"，别贴在一起
NAME_DY = 1              # 名称相对行内的偏移
DESC_DY = 21             # 说明相对行内的偏移（必须 >= NAME_DY + 名称高度）
FOOTER_H = 22            # 底部放操作提示，避免和标题挤在同一行
PAD = 8
VISIBLE_ROWS = 5         # 同时显示几行；条目更多时自动出现滚动条（滚轮滚动）
SHADOW_PAD = 12

# ---- 缓动 / 缓存 ----
EASE_SPEED = 15.0        # 开合缓动速度
SCROLL_SPEED = 14.0      # 滚动缓动速度
SCROLL_STEP = 1.0        # 一格滚轮滚几行
CACHE_STEPS = 20         # 开合缓动量化档数
SUB_STEPS = 4            # 滚动/悬停的亚档细分（每行 4 档）

TITLE = "结构"
HINT = "滚轮滚动 · 左键选择 · Ctrl+Z 撤销 · F/Esc 关闭"

# 可生成的结构：(id, 名称, 说明)
STRUCTURES = (
    ("wall_h", "横墙", "横向木墙 · 拖拽定长宽"),
    ("wall_v", "竖墙", "纵向木墙 · 拖拽定长高"),
    ("house", "房子", "地板 + 四面墙 · 默认高 3"),
    ("hall_ns", "竖向连廊", "地板 + 左右竖墙 · 南北开口"),
    ("hall_ew", "横向连廊", "地板 + 上下横墙 · 东西开口"),
    ("floor", "地板", "砖地板 · 拖拽定格数"),
    ("table", "木桌", "木桌 · 拖拽定格数"),
)


def _ease_out_back(t):
    """回弹收尾：结尾稍微"过冲"一下，弹出感更自然。"""
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


def _approach(cur, target, dt, speed):
    diff = target - cur
    if abs(diff) > 0.0001:
        cur += diff * (1.0 - math.exp(-speed * dt))
        if abs(cur - target) < 0.0001:
            cur = target
    else:
        cur = target
    return cur


class StructurePalette:
    """鼠标旁弹出的结构选择面板。"""

    _surf_cache = {}
    _grad_cache = {}   # 行底实心渐变缓存
    _mask_cache = {}   # 圆角掩膜缓存

    def __init__(self, on_select=None):
        self.on_select = on_select
        self.open = False
        self.visible = False          # 完全关掉后为 False（不必再绘制）

        self._ease = 0.0              # 开合进度 0~1
        self._pos = [0, 0]
        self._rect = pygame.Rect(0, 0, 0, 0)

        self._scroll = 0.0
        self._scroll_target = 0.0
        self._hover = -1
        self._hover_ease = 0.0
        self.selected = STRUCTURES[0][0]

        self._surf = None
        self._surf_key = None
        self._last_ticks = pygame.time.get_ticks()

    # ------------------------------------------------------------------ #
    # 开关
    # ------------------------------------------------------------------ #
    def toggle(self, mouse_pos, screen_rect):
        if self.open:
            self.close()
        else:
            self.open_at(mouse_pos, screen_rect)
        return self.open

    def open_at(self, mouse_pos, screen_rect):
        self._layout(mouse_pos, screen_rect)
        self.open = True
        self.visible = True
        self._last_ticks = pygame.time.get_ticks()
        # 打开时把已选项滚进可视区
        idx = self._selected_index()
        self._scroll = self._scroll_target = self._clamp_scroll(idx - VISIBLE_ROWS // 2)

    def close(self):
        self.open = False

    @property
    def rect(self):
        return self._rect.copy()

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _row_count(self):
        return len(STRUCTURES)

    def _max_scroll(self):
        return max(0.0, float(self._row_count() - VISIBLE_ROWS))

    def _clamp_scroll(self, v):
        return max(0.0, min(float(v), self._max_scroll()))

    def _panel_size(self):
        rows = min(self._row_count(), VISIBLE_ROWS)
        return WIDTH, HEADER_H + rows * ROW_H + FOOTER_H + PAD

    def _layout(self, mouse_pos, screen_rect):
        w, h = self._panel_size()
        x = mouse_pos[0] + 16
        y = mouse_pos[1] - HEADER_H // 2
        if screen_rect is not None:
            x = max(8, min(x, screen_rect.width - w - 8))
            y = max(8, min(y, screen_rect.height - h - 8))
        self._pos = [int(x), int(y)]
        self._rect = pygame.Rect(int(x), int(y), w, h)

    def _selected_index(self):
        for i, (sid, _n, _d) in enumerate(STRUCTURES):
            if sid == self.selected:
                return i
        return 0

    # ------------------------------------------------------------------ #
    # 更新
    # ------------------------------------------------------------------ #
    def update(self, dt, mouse_pos=None):
        if self._ease <= 0.0 and not self.open:
            self.visible = False
            return

        target = 1.0 if self.open else 0.0
        self._ease = _approach(self._ease, target, dt, EASE_SPEED)
        self._scroll = _approach(self._scroll, self._scroll_target, dt, SCROLL_SPEED)

        if self.open and mouse_pos is not None:
            self._hover = self._row_at(mouse_pos)
        else:
            self._hover = -1
        self._hover_ease = _approach(
            self._hover_ease, 1.0 if self._hover >= 0 else 0.0, dt,
            EASE_SPEED * 1.6)

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def _row_at(self, pos):
        """pos 命中的结构下标；不在面板内返回 -1。"""
        if not self._rect.collidepoint(pos):
            return -1
        local_y = pos[1] - self._rect.y - HEADER_H
        if local_y < 0:
            return -1
        row = int(local_y // ROW_H) + int(round(self._scroll))
        if 0 <= row < self._row_count():
            return row
        return -1

    def handle_event(self, event):
        """返回 True 表示事件被面板消费。"""
        if not self.open:
            return False

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return True

        if event.type == pygame.MOUSEWHEEL:
            # 滚轮在面板打开时归列表，不再缩放镜头
            self._scroll_target = self._clamp_scroll(
                self._scroll_target - event.y * SCROLL_STEP)
            return True

        if event.type == pygame.MOUSEMOTION:
            return self._rect.collidepoint(event.pos)

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:
                row = self._row_at(event.pos)
                if row >= 0:
                    self._choose(row)
                elif not self._rect.collidepoint(event.pos):
                    self.close()
            # 面板开着时吃掉鼠标按下，避免点穿到世界里去
            return True

        if event.type == pygame.MOUSEBUTTONUP:
            return self._rect.collidepoint(event.pos)

        return False

    def _choose(self, index):
        sid = STRUCTURES[index][0]
        self.selected = sid
        self.close()
        if self.on_select is not None:
            self.on_select(sid)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def draw(self, surface, mouse_pos=None):
        if not self.visible or self._ease <= 0.001:
            return

        ease = self._ease
        scroll_i = int(round(self._scroll))
        # 量化进缓存键：档位内像素完全一致，静止帧只 blit。
        # 【注意】凡是 _compose 里读到的状态都必须进键，否则会复用旧表面 ——
        # 表现就是"滚动/悬停时还显示上一次的内容"（文字残影）。
        scroll_q = int(round((self._scroll - scroll_i) * SUB_STEPS))
        hover_q = int(round(self._hover_ease * SUB_STEPS))
        step = int(ease * CACHE_STEPS + 0.5)
        key = (step, scroll_i, scroll_q, hover_q, self._hover, self.selected,
               self._pos[0], self._pos[1])
        if key != self._surf_key:
            self._surf = self._compose(step / CACHE_STEPS, scroll_i,
                                      scroll_q / SUB_STEPS, hover_q / SUB_STEPS)
            self._surf_key = key

        surface.blit(self._surf, (self._pos[0] - SHADOW_PAD,
                                  self._pos[1] - SHADOW_PAD))

    def _compose(self, ease, scroll_i, scroll_frac=0.0, hover_ease=1.0):
        """合成面板（含投影预留边距）。按量化档缓存，所以这里可以画得细一点。"""
        w, h = self._panel_size()
        surf = pygame.Surface((w + SHADOW_PAD * 2, h + SHADOW_PAD * 2),
                              pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        body = pygame.Rect(SHADOW_PAD, SHADOW_PAD, w, h)

        alpha = int(255 * ease)
        title_font = get_font(15)
        row_font = get_font(15)
        hint_font = get_font(11)

        # ---- 投影 + 卡片 ----
        theme.draw_card(surf, body, radius=theme.RADIUS_CARD,
                        bg=theme.CARD_BG, border=theme.BORDER,
                        shadow=10, shadow_alpha=int(120 * ease),
                        tint_alpha=alpha)

        # ---- 标题栏：标题在左，条目数在右（长提示挪到页脚，免得和标题打架）----
        pygame.draw.rect(surf, (*theme.ACCENT_HI, alpha),
                         (body.x + 12, body.y + 13, 3, 13), border_radius=2)
        self._blit_text(surf, title_font, TITLE, theme.TEXT,
                        (body.x + 22, body.y + 10), alpha)
        # 条目数右对齐：只渲染不 blit，否则会在面板左上角 (0,0) 多留一份
        count = self._render_text(hint_font, f"{self._row_count()} 项",
                                  theme.TEXT_MUTED, alpha)
        surf.blit(count, (body.right - 12 - count.get_width(), body.y + 15))
        pygame.draw.line(surf, (*theme.BORDER, alpha),
                         (body.x, body.y + HEADER_H - 1),
                         (body.right, body.y + HEADER_H - 1), 1)

        # ---- 列表：clamp 到面板内 ----
        list_rect = pygame.Rect(body.x, body.y + HEADER_H, w,
                                h - HEADER_H - FOOTER_H - PAD)
        old_clip = surf.get_clip()
        surf.set_clip(list_rect)

        rows = self._row_count()
        for vis in range(VISIBLE_ROWS + 1):
            row = scroll_i + vis
            if row < 0 or row >= rows:
                continue
            sid, name, desc = STRUCTURES[row]
            y = list_rect.y + vis * ROW_H - int(scroll_frac * ROW_H)
            r = pygame.Rect(body.x + PAD, y + 2, w - PAD * 2, ROW_H - 4)

            chosen = (sid == self.selected)
            hovered = (row == self._hover)

            # 行底一律用"实心"的：不再让卡片底色透上来（半透明材质看着很脏）。
            # 选中 = 强调色的竖向渐变，悬停 = 稍亮的实心中性面，强度跟随缓动。
            if chosen:
                self._rounded_fill(surf, r, theme.RADIUS_ROW,
                                   (*self._shade(theme.ACCENT, 1.65), alpha),
                                   (*theme.ACCENT, alpha))
                pygame.draw.rect(surf, (*theme.ACCENT_HI, alpha),
                                 (r.x + 5, r.centery - 8, 3, 16), border_radius=2)
            elif hovered:
                k = 0.5 + 0.5 * hover_ease          # 缓动强度，不让颜色"啪"地跳
                top = self._mix(theme.CARD_BG, theme.CARD_BG_HI, k)
                self._rounded_fill(surf, r, theme.RADIUS_ROW,
                                   (*self._shade(top, 1.14), alpha), (*top, alpha))
                pygame.draw.rect(surf, (*theme.BORDER_HI, alpha), r, width=1,
                                 border_radius=theme.RADIUS_ROW)

            name_col = theme.TEXT if (chosen or hovered) else theme.TEXT_DIM
            # 名称与说明分开排：行高 40 才放得下两行字，偏移量见 NAME_DY / DESC_DY
            self._blit_text(surf, row_font, name, name_col,
                            (r.x + 14, r.y + NAME_DY), alpha)
            self._blit_text(surf, hint_font, desc,
                            theme.TEXT if chosen else theme.TEXT_MUTED,
                            (r.x + 14, r.y + DESC_DY), alpha)

        surf.set_clip(old_clip)

        # ---- 底部操作提示 ----
        pygame.draw.line(surf, (*theme.BORDER, alpha),
                         (body.x, list_rect.bottom + 1),
                         (body.right, list_rect.bottom + 1), 1)
        self._blit_text(surf, hint_font, HINT, theme.TEXT_MUTED,
                        (body.x + 12, list_rect.bottom + 4), alpha)

        # ---- 滚动指示条（内容超过可视行数时才画） ----
        if rows > VISIBLE_ROWS:
            track = pygame.Rect(body.right - 5, list_rect.y + 2, 3,
                                list_rect.height - 4)
            pygame.draw.rect(surf, (*theme.CARD_BG_HI, alpha), track,
                             border_radius=2)
            frac = VISIBLE_ROWS / rows
            th = max(16, int(track.height * frac))
            denom = max(1.0, self._max_scroll())
            ty = track.y + int((track.height - th) * (self._scroll / denom))
            pygame.draw.rect(surf, (*theme.ACCENT_HI, alpha),
                             (track.x, ty, 3, th), border_radius=2)

        return surf

    def invalidate(self):
        self._surf = None
        self._surf_key = None

    # ------------------------------------------------------------------ #
    # 绘制小工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render_text(font, text, color, alpha):
        """渲染文字并套上面板 alpha（只渲染，不 blit）。"""
        s = font.render(text, True, color)
        if alpha < 255:
            s.set_alpha(alpha)
        return s

    @classmethod
    def _blit_text(cls, surf, font, text, color, pos, alpha):
        """按面板的整体 alpha 把文字画到 pos，并返回渲染出来的表面。

        【为什么必须 set_alpha】``font.render`` 出来的表面自身 alpha 就是 255，
        直接 blit 的话文字完全不吃面板的淡入淡出 —— 于是面板淡出/关掉之后，
        卡片没了、**字还全不透明地留在屏幕上**（"框消失但文字残留"就是这么来的）。

        【注意】这个函数自己会 blit。需要在别处再摆位的（比如右对齐的条目数），
        要用 _render_text() 只渲染，否则会先在 (0,0) 多画一份。
        """
        s = cls._render_text(font, text, color, alpha)
        surf.blit(s, pos)
        return s

    @staticmethod
    def _shade(color, k):
        return tuple(min(255, max(0, int(c * k))) for c in color[:3])

    @staticmethod
    def _mix(c1, c2, t):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    @classmethod
    def _mask(cls, w, h, radius):
        """圆角掩膜（白=保留），按尺寸缓存。"""
        key = (w, h, radius)
        m = cls._mask_cache.get(key)
        if m is None:
            m = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
            pygame.draw.rect(m, (255, 255, 255, 255), (0, 0, w, h),
                             border_radius=radius)
            if len(cls._mask_cache) >= 64:
                cls._mask_cache.clear()
            cls._mask_cache[key] = m
        return m

    @classmethod
    def _rounded_fill(cls, surf, rect, radius, top_col, bot_col):
        """实心的竖向渐变圆角块（2 像素种子 + smoothscale，插值全在 C 侧）。"""
        key = (rect.width, rect.height, tuple(top_col), tuple(bot_col))
        grad = cls._grad_cache.get(key)
        if grad is None:
            seed = pygame.Surface((1, 2), pygame.SRCALPHA)
            seed.set_at((0, 0), tuple(top_col))
            seed.set_at((0, 1), tuple(bot_col))
            grad = pygame.transform.smoothscale(
                seed, (max(1, rect.width), max(1, rect.height)))
            grad.blit(cls._mask(rect.width, rect.height, radius), (0, 0),
                      special_flags=pygame.BLEND_RGBA_MULT)
            if len(cls._grad_cache) >= 64:
                cls._grad_cache.clear()
            cls._grad_cache[key] = grad
        surf.blit(grad, rect.topleft)
