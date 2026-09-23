import pygame
import math

HOVER_STEPS = 64


def _lerp(c1, c2, t):
    n = min(len(c1), len(c2))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(n))


class Scrollbar:
    """现代渐变滚动条（垂直 / 水平）。

    渐变方向沿控件的**长边**：
        - 水平滚动条（宽 > 高）：渐变沿水平方向，从滑道左端到右端
        - 竖直滚动条（高 > 宽）：渐变沿竖直方向，从滑道上端到下端
    这样渐变跨度是 100~400px 级别，颜色过渡清晰可见。
    """

    _grad_cache = {}
    _surf_cache = {}

    # 缓存容量上限：滚动条的几何 / 可见比例 / 悬停档位组合可以很多，
    # 不设上限的话历史表面会一直堆积（长时间运行内存单调增长）。
    _GRAD_CACHE_LIMIT = 64
    _SURF_CACHE_LIMIT = 128

    def __init__(self, value=0.0, speed=12.0):
        self._value = min(1.0, max(0.0, float(value)))
        self._pos = self._value
        self._visible_ratio = 1.0
        self._hover = False
        self._dragging = False
        self._hover_progress = 0.0
        self._speed = speed

        self._track_rect = pygame.Rect(0, 0, 0, 0)
        self._thumb_rect = pygame.Rect(0, 0, 0, 0)
        self._orientation = "vertical"
        self._padding = 2
        self._travel = 0.0
        self._grab_delta = 0.0

        self._surf = None
        self._surf_key = None

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self.set_value(v)

    def set_value(self, v):
        v = float(v)
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        self._value = v

    def scroll_by(self, delta):
        self.set_value(self._value + float(delta))

    def set_visible_ratio(self, ratio):
        ratio = float(ratio)
        self._visible_ratio = min(1.0, max(0.02, ratio))

    @property
    def visible_ratio(self):
        return self._visible_ratio

    @property
    def dragging(self):
        return self._dragging

    def set_hover(self, hover):
        self._hover = bool(hover)

    def hit_test(self, pos):
        return self._track_rect.collidepoint(pos)

    def invalidate(self):
        """兼容旧接口：现在无需手动失效缓存。

        优化后滑道 / 滑块的表面由 (尺寸, 圆角, 颜色) 内容寻址，颜色本身已按
        悬停档位量化 —— 内容没变就必然命中，内容变了键自然不同，不存在"脏缓存"。
        """
        self._surf = None
        self._surf_key = None

    # ------------------------------------------------------------------ #
    # 更新
    # ------------------------------------------------------------------ #
    def update(self, dt):
        if self._dragging:
            self._pos = self._value
        else:
            diff = self._value - self._pos
            if abs(diff) > 0.0001:
                self._pos += diff * (1.0 - math.exp(-self._speed * dt))
                if abs(self._pos - self._value) < 0.0001:
                    self._pos = self._value
            else:
                self._pos = self._value

        target_hover = 1.0 if (self._hover or self._dragging) else 0.0
        diff_h = target_hover - self._hover_progress
        if abs(diff_h) > 0.0001:
            self._hover_progress += diff_h * (1.0 - math.exp(-self._speed * 1.2 * dt))
            if abs(self._hover_progress - target_hover) < 0.0001:
                self._hover_progress = target_hover
        else:
            self._hover_progress = target_hover

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    @staticmethod
    def _axis(pair, vertical):
        return pair[1] if vertical else pair[0]

    def handle_event(self, event):
        if self._track_rect.width <= 0 or self._track_rect.height <= 0:
            return False
        vertical = (self._orientation == "vertical")

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._thumb_rect.collidepoint(event.pos):
                self._dragging = True
                self._grab_delta = (self._axis(event.pos, vertical)
                                    - self._axis(self._thumb_rect.topleft, vertical))
                return True
            if self._track_rect.collidepoint(event.pos):
                thumb_len = self._axis(self._thumb_rect.size, vertical)
                self._grab_delta = thumb_len / 2.0
                self._drag_to(event.pos)
                self._dragging = True
                return True
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._dragging:
                self._dragging = False
                return True
            return False

        if event.type == pygame.MOUSEMOTION and self._dragging:
            self._drag_to(event.pos)
            return True

        return False

    def _drag_to(self, pos):
        if self._travel <= 0:
            return
        vertical = (self._orientation == "vertical")
        base = self._axis(self._track_rect.topleft, vertical) + self._padding
        start = self._axis(pos, vertical) - self._grab_delta
        self.set_value((start - base) / self._travel)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def draw(self, surface, rect, orientation="vertical",
             track_a=(52, 58, 78), track_b=(26, 28, 40),
             track_hover_a=(72, 80, 104), track_hover_b=(38, 42, 58),
             thumb_a=(128, 156, 255), thumb_b=(66, 96, 216),
             thumb_hover_a=(168, 190, 255), thumb_hover_b=(96, 126, 240),
             border_radius=None, padding=2, min_thumb=24):
        if rect.width <= 0 or rect.height <= 0:
            return

        W, H = int(rect.width), int(rect.height)
        vertical = (orientation == "vertical")
        radius = min(W, H) // 2 if border_radius is None else int(border_radius)
        radius = max(1, radius)
        direction = "v" if vertical else "h"

        # 悬停进度先量化再取色：同一档位内颜色完全一致，缓存才能稳定命中。
        # 旧实现拿连续值取色，每个中间档都算出"新颜色"，缓存只增不减。
        t = int(self._hover_progress * HOVER_STEPS + 0.5) / HOVER_STEPS

        # ---- 滑道：只与几何 + 悬停色有关，与滚动位置无关 ----
        # 旧实现把「滑道 + 滑块」合成成一整张表面并按 pos_step 缓存，
        # 于是每滚动一个像素就要新建一张 WxH 表面重新合成一次（一次滚动几百次）。
        # 现在滑道的表面直接复用 _rounded_grad 的全局缓存，滚动完全不动它。
        track_surf = self._rounded_grad(
            W, H, radius,
            _lerp(track_a, track_hover_a, t), _lerp(track_b, track_hover_b, t),
            direction)
        surface.blit(track_surf, (int(rect.x), int(rect.y)))

        # ---- 滑块：位置每帧单独算，只做一次 blit（同样直接命中缓存） ----
        thumb, _avail, travel = self._thumb_rect_local(
            W, H, orientation, padding, min_thumb)
        tw = max(1, int(round(thumb.width)))
        th = max(1, int(round(thumb.height)))
        tr = max(1, min(tw, th) // 2)
        thumb_surf = self._rounded_grad(
            tw, th, tr,
            _lerp(thumb_a, thumb_hover_a, t), _lerp(thumb_b, thumb_hover_b, t),
            direction)
        surface.blit(thumb_surf, (int(rect.x) + thumb.x, int(rect.y) + thumb.y))

        # ---- 记录几何，供事件命中测试 ----
        self._orientation = orientation
        self._padding = padding
        self._track_rect = pygame.Rect(int(rect.x), int(rect.y), W, H)
        self._thumb_rect = thumb.move(int(rect.x), int(rect.y))
        self._travel = travel

    # ------------------------------------------------------------------ #
    # 合成
    # ------------------------------------------------------------------ #
    def _thumb_rect_local(self, width, height, orientation, padding, min_thumb):
        vertical = (orientation == "vertical")
        if vertical:
            avail = max(1.0, height - 2 * padding)
            thick = max(1.0, width - 2 * padding)
        else:
            avail = max(1.0, width - 2 * padding)
            thick = max(1.0, height - 2 * padding)

        thumb_len = avail * self._visible_ratio
        thumb_len = max(thumb_len, float(min_thumb))
        thumb_len = min(thumb_len, avail)
        travel = avail - thumb_len
        off = padding + self._pos * travel

        if vertical:
            rect = pygame.Rect(padding, off, thick, thumb_len)
        else:
            rect = pygame.Rect(off, padding, thumb_len, thick)
        return rect, avail, travel

    # ------------------------------------------------------------------ #
    # 渐变工具
    # ------------------------------------------------------------------ #
    @classmethod
    def _get_1px_grad(cls, length, c_a, c_b, horizontal):
        key = (length, tuple(c_a), tuple(c_b), horizontal)
        g = cls._grad_cache.get(key)
        if g is None:
            length = max(2, int(length))
            # 关键优化：线性渐变直接由「2 像素种子 + smoothscale」得到。
            # 旧实现是对 length 个像素逐点 set_at —— 在 4 倍超采样下
            # 单条滚动条就要跑 4*H 次 Python 循环（H=1200 时是 4800 次），
            # 冷启动一帧能干到 8ms+。这里插值全部在 C 侧完成，只写 2 个像素。
            if horizontal:
                seed = pygame.Surface((2, 1), pygame.SRCALPHA)
                seed.set_at((0, 0), c_a)
                seed.set_at((1, 0), c_b)
                g = pygame.transform.smoothscale(seed, (length, 1))
            else:
                seed = pygame.Surface((1, 2), pygame.SRCALPHA)
                seed.set_at((0, 0), c_a)
                seed.set_at((0, 1), c_b)
                g = pygame.transform.smoothscale(seed, (1, length))
            if len(cls._grad_cache) >= cls._GRAD_CACHE_LIMIT:
                cls._grad_cache.clear()
            cls._grad_cache[key] = g
        return g

    @classmethod
    def _rounded_grad(cls, w, h, radius, c_a, c_b, direction):
        key = (w, h, radius, tuple(c_a), tuple(c_b), direction)
        surf = cls._surf_cache.get(key)
        if surf is not None:
            return surf

        AA = 3
        gw = max(2, w * AA)
        gh = max(2, h * AA)

        # 渐变是线性的：直接在目标尺寸生成，没必要按 4 倍建大表面再缩回来
        # （旧实现把 gw x gh 的渐变整张算出来，再整张乘掩膜，白做一遍超采样）
        if direction == "h":
            base = cls._get_1px_grad(w, c_a, c_b, True)
        else:
            base = cls._get_1px_grad(h, c_a, c_b, False)
        grad = pygame.transform.smoothscale(base, (w, h))

        # 真正需要超采样的只有圆角边缘：掩膜仍按 AA 倍画，再缩回目标尺寸
        mask_big = pygame.Surface((gw, gh), pygame.SRCALPHA)
        pygame.draw.rect(mask_big, (255, 255, 255, 255),
                         (0, 0, gw, gh), border_radius=radius * AA)
        mask = pygame.transform.smoothscale(mask_big, (w, h))
        grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        surf = grad
        if len(cls._surf_cache) >= cls._SURF_CACHE_LIMIT:
            cls._surf_cache.clear()
        cls._surf_cache[key] = surf
        return surf