import math
import random
import pygame

# 悬停等慢变量用小档位即可；位置不再量化
CACHE_STEPS = 48


class Slider:
    """水平滑块。

    设计要点（与 Switch 一致的思路，但针对长条控件做了优化）：
      - 静态部分（未填充轨道 / 已填充渐变轨道）按尺寸缓存成整张表面，
        每帧只做 blit，位置用浮点直接定位，无量化台阶；
      - 滑块本身按悬停档位缓存；
      - 缓动用"定时长 + ease-out-cubic"，收尾速度趋近 0，不生硬；
      - 滑轨已填充段为多停靠点水平渐变，色相跨度大，视觉明显。

    speed 的作用：
      - 拖动时为了跟手，_pos 直接跟随鼠标，speed 不参与；
      - 程序化赋值（set_value / value = x）时按
            duration = (0.12 + 0.28 * 距离) * (12 / speed)
        启动补间，speed 越大越快。
    """

    _vgrad_cache = {}   # 垂直渐变：(height, top, bottom)
    _hgrad_cache = {}   # 水平渐变：(width, stops)

    # 默认填充渐变：蓝紫 -> 蓝 -> 青绿 -> 绿（色相跨度大，肉眼明显）
    _DEFAULT_GRAD = (
        (0.00, (92, 116, 255)),
        (0.35, (64, 176, 255)),
        (0.70, (48, 220, 190)),
        (1.00, (128, 236, 112)),
    )

    def __init__(self, value=0.0, min_value=0.0, max_value=1.0, speed=12.0):
        if max_value <= min_value:
            raise ValueError("max_value 必须大于 min_value")
        self._min = float(min_value)
        self._max = float(max_value)
        self._target = self._clamp(float(value))
        self._pos = self._to_norm(self._target)
        self._hover = False
        self._hover_progress = 0.0
        self._dragging = False
        self._speed = float(speed)

        # 补间状态
        self._anim_from = self._pos
        self._anim_to = self._pos
        self._anim_t = 1.0          # 1.0 表示已完成
        self._anim_dur = 0.1

        # 表面缓存
        self._base_key = None
        self._track_off_surf = None
        self._track_grad_surf = None
        self._knob_key = None
        self._knob_surf = None

    # ------------------------------------------------------------------ 取值 / 设值
    @property
    def value(self):
        return self._target

    @value.setter
    def value(self, v):
        self.set_value(v)

    @property
    def normalized(self):
        return self._to_norm(self._target)

    @property
    def dragging(self):
        return self._dragging

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0.5, float(v))

    def set_value(self, v, immediate=False):
        """设置目标值。

        immediate=False（默认）：按 speed 决定的时长做 ease-out 补间；
        immediate=True：直接吸附。
        """
        v = self._clamp(float(v))
        n = self._to_norm(v)
        self._target = v
        if immediate:
            self._pos = n
            self._anim_from = self._anim_to = n
            self._anim_t = 1.0
        else:
            self._start_tween(n)
        return v

    def _start_tween(self, n):
        self._anim_from = self._pos
        self._anim_to = n
        self._anim_t = 0.0
        dist = abs(n - self._pos)
        # 距离越远时长越长，speed 整体缩放
        base = 0.12 + 0.28 * dist
        self._anim_dur = max(0.04, base * (12.0 / max(0.5, self._speed)))

    def _clamp(self, v):
        return self._min if v < self._min else (self._max if v > self._max else v)

    def _to_norm(self, v):
        return (v - self._min) / (self._max - self._min)

    def _from_norm(self, n):
        return self._min + n * (self._max - self._min)

    # ------------------------------------------------------------------ 交互
    def set_hover(self, hover):
        self._hover = bool(hover)

    def handle_event(self, event, rect):
        """处理鼠标事件。返回 True 表示该事件被滑块消费。"""
        if rect.width <= 0 or rect.height <= 0:
            return False

        _, x0, x1 = self._layout(rect.width, rect.height)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if rect.collidepoint(event.pos):
                self._dragging = True
                self._hover = True
                self._set_from_x(event.pos[0], rect, x0, x1)
                return True

        elif event.type == pygame.MOUSEMOTION:
            if self._dragging:
                self._set_from_x(event.pos[0], rect, x0, x1)
                self._hover = True
                return True
            self._hover = rect.collidepoint(event.pos)

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._dragging:
                self._dragging = False
                self._hover = rect.collidepoint(event.pos)
                return True

        return False

    def _set_from_x(self, mx, rect, x0, x1):
        if x1 <= x0:
            n = 0.0
        else:
            n = (mx - rect.x - x0) / (x1 - x0)
        n = 0.0 if n < 0.0 else (1.0 if n > 1.0 else n)
        self._pos = n
        self._target = self._from_norm(n)
        self._anim_from = self._anim_to = n
        self._anim_t = 1.0

    # ------------------------------------------------------------------ 更新
    def update(self, dt):
        if self._dragging:
            # 拖拽时瞬时跟随，无缓动
            n = self._to_norm(self._target)
            self._pos = n
            self._anim_from = self._anim_to = n
            self._anim_t = 1.0
        elif self._anim_t < 1.0:
            self._anim_t = min(1.0, self._anim_t + dt / self._anim_dur)
            e = self._ease_out_cubic(self._anim_t)
            self._pos = self._anim_from + (self._anim_to - self._anim_from) * e

        # 悬停进度（小范围慢变量，指数缓动足够）
        target_hover = 1.0 if self._hover else 0.0
        diff_h = target_hover - self._hover_progress
        if abs(diff_h) > 0.0008:
            self._hover_progress += diff_h * (1.0 - math.exp(-self._speed * 1.5 * dt))
        else:
            self._hover_progress = target_hover

    @staticmethod
    def _ease_out_cubic(t):
        """三次缓出：末速度趋近 0，收尾干净不硬。"""
        u = 1.0 - t
        return 1.0 - u * u * u

    @staticmethod
    def _step(value):
        return int(value * CACHE_STEPS + 0.5)

    def invalidate(self):
        """强制丢弃缓存（一般不需要手动调用）。"""
        self._base_key = None
        self._knob_key = None

    # ------------------------------------------------------------------ 布局
    @staticmethod
    def _layout(width, height):
        """返回 (最大滑块半径, 行程起点 x, 行程终点 x)，均为最终像素坐标。"""
        max_r = height * 0.36 * 1.08
        x0 = max_r
        x1 = width - max_r
        if x1 <= x0:
            cx = width * 0.5
            x0 = x1 = cx
        return max_r, x0, x1

    # ------------------------------------------------------------------ 绘制
    def draw(self, surface, rect,
             track_off=(52, 54, 68),
             track_grad=None,
             knob_color=(232, 232, 236),
             knob_hover_color=(255, 255, 255)):
        """绘制滑块。

        track_grad: 填充渐变的停靠点，形如
            [(0.0, (r,g,b)), (0.5, (r,g,b)), (1.0, (r,g,b))]
        传 None 时使用内置的蓝紫->蓝->青绿->绿。
        """
        if rect.width <= 0 or rect.height <= 0:
            return

        if track_grad is None:
            track_grad = self._DEFAULT_GRAD
        stops = tuple((float(p), tuple(int(c) for c in col)) for p, col in track_grad)

        # ---- 静态层：未填充轨道 + 已填充渐变轨道 ----
        base_key = (rect.width, rect.height, tuple(track_off), stops)
        if base_key != self._base_key:
            self._track_off_surf, self._track_grad_surf = self._build_base(
                rect.width, rect.height, track_off, stops)
            self._base_key = base_key

        # ---- 滑块层：按悬停档位缓存 ----
        hover_step = self._step(self._hover_progress)
        knob_key = (rect.height, hover_step,
                    tuple(knob_color), tuple(knob_hover_color))
        if knob_key != self._knob_key:
            self._knob_surf = self._build_knob(
                rect.height, hover_step, knob_color, knob_hover_color)
            self._knob_key = knob_key

        # ---- 每帧只做 3 次 blit ----
        _, x0, x1 = self._layout(rect.width, rect.height)
        knob_x = x0 + self._pos * (x1 - x0)

        ox, oy = int(rect.x), int(rect.y)
        surface.blit(self._track_off_surf, (ox, oy))

        fill_w = int(knob_x - x0)
        if fill_w > 0:
            old_clip = surface.get_clip()
            surface.set_clip(pygame.Rect(ox + int(x0), oy, fill_w, rect.height))
            surface.blit(self._track_grad_surf, (ox, oy))
            surface.set_clip(old_clip)

        kd = self._knob_surf.get_width()
        surface.blit(self._knob_surf,
                     (ox + int(knob_x - kd * 0.5), oy + (rect.height - kd) // 2))

    # ------------------------------------------------------------------ 静态层构建
    def _build_base(self, width, height, track_off, stops):
        """构建两张全尺寸表面：未填充轨道、已填充渐变轨道。只做一次。"""
        AA = 4
        W = max(1, width * AA)
        H = max(1, height * AA)

        _, x0f, x1f = self._layout(width, height)
        tx0 = int(x0f * AA)
        tx1 = int(x1f * AA)
        tw = max(0, tx1 - tx0)

        track_h = max(2.0, height * 0.34)
        ty = int((height - track_h) * 0.5 * AA)
        th = max(1, int(track_h * AA))
        tr_aa = max(1, int(track_h * 0.5 * AA))

        vg = self._get_vgradient(H, (255, 255, 255, 255), (200, 200, 200, 255))
        vg_scaled = pygame.transform.scale(vg, (W, H))

        # ---- 未填充轨道 ----
        off = pygame.Surface((W, H), pygame.SRCALPHA)
        off.fill((0, 0, 0, 0))
        if tw > 0:
            pygame.draw.rect(off, track_off, (tx0, ty, tw, th), border_radius=tr_aa)
        off.blit(vg_scaled, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # ---- 已填充渐变轨道 ----
        grad = pygame.Surface((W, H), pygame.SRCALPHA)
        grad.fill((0, 0, 0, 0))
        if tw > 0:
            hg = self._get_hgradient(tw, stops)
            hg_full = pygame.transform.scale(hg, (tw, th))
            mask = pygame.Surface((tw, th), pygame.SRCALPHA)
            mask.fill((0, 0, 0, 0))
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, tw, th),
                             border_radius=tr_aa)
            hg_full.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            grad.blit(hg_full, (tx0, ty))
        grad.blit(vg_scaled, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        return (pygame.transform.smoothscale(off, (width, height)),
                pygame.transform.smoothscale(grad, (width, height)))

    # ------------------------------------------------------------------ 滑块构建
    def _build_knob(self, height, hover_step, knob_color, knob_hover_color):
        AA = 4
        hover = hover_step / CACHE_STEPS
        r = height * 0.36 * (1.0 + 0.08 * hover)
        d = max(4, int(r * 2))
        D = d * AA

        surf = pygame.Surface((D, D), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))

        c = self._lerp_color(knob_color, knob_hover_color, hover)
        pygame.draw.rect(surf, c, (0, 0, D, D), border_radius=D // 2)

        # 垂直明暗：底部压暗，做出圆柱感
        vg = self._get_vgradient(D, (255, 255, 255, 255), (172, 172, 172, 255))
        surf.blit(pygame.transform.scale(vg, (D, D)), (0, 0),
                  special_flags=pygame.BLEND_RGBA_MULT)

        # 顶部高光：alpha=0，只加 RGB，不改透明度
        ag = self._get_vgradient(D, (72, 72, 72, 0), (0, 0, 0, 0))
        surf.blit(pygame.transform.scale(ag, (D, D)), (0, 0),
                  special_flags=pygame.BLEND_RGBA_ADD)

        return pygame.transform.smoothscale(surf, (d, d))

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _lerp_color(c1, c2, t):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    @classmethod
    def _get_vgradient(cls, height, color_top, color_bottom):
        key = (height, color_top, color_bottom)
        surf = cls._vgrad_cache.get(key)
        if surf is None:
            surf = cls._make_vgradient(height, color_top, color_bottom)
            cls._vgrad_cache[key] = surf
        return surf

    @staticmethod
    def _make_vgradient(height, color_top, color_bottom):
        surf = pygame.Surface((1, height), pygame.SRCALPHA)
        for y in range(height):
            t = y / (height - 1) if height > 1 else 0
            r = int(color_top[0] + (color_bottom[0] - color_top[0]) * t)
            g = int(color_top[1] + (color_bottom[1] - color_top[1]) * t)
            b = int(color_top[2] + (color_bottom[2] - color_top[2]) * t)
            a = int(color_top[3] + (color_bottom[3] - color_top[3]) * t) \
                if len(color_top) > 3 else 255
            surf.set_at((0, y), (r, g, b, a))
        return surf

    @classmethod
    def _get_hgradient(cls, width, stops):
        key = (width, stops)
        surf = cls._hgrad_cache.get(key)
        if surf is None:
            surf = cls._make_hgradient(width, stops)
            cls._hgrad_cache[key] = surf
        return surf

    @staticmethod
    def _make_hgradient(width, stops):
        width = max(1, width)
        surf = pygame.Surface((width, 1), pygame.SRCALPHA)
        if width == 1:
            c = stops[0][1]
            surf.set_at((0, 0), (c[0], c[1], c[2], 255))
            return surf
        for x in range(width):
            t = x / (width - 1)
            surf.set_at((x, 0), Slider._sample_stops(stops, t))
        return surf

    @staticmethod
    def _sample_stops(stops, t):
        """在多停靠点渐变上采样，段内用 smoothstep 让过渡更柔和。"""
        if t <= stops[0][0]:
            c = stops[0][1]
            return (c[0], c[1], c[2], 255)
        if t >= stops[-1][0]:
            c = stops[-1][1]
            return (c[0], c[1], c[2], 255)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                u = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
                u = u * u * (3.0 - 2.0 * u)   # smoothstep
                return (int(c0[0] + (c1[0] - c0[0]) * u),
                        int(c0[1] + (c1[1] - c0[1]) * u),
                        int(c0[2] + (c1[2] - c0[2]) * u),
                        255)
        c = stops[-1][1]
        return (c[0], c[1], c[2], 255)


# ---------------------------------------------------------------------- 演示
if __name__ == "__main__":
    pygame.init()
    screen = pygame.display.set_mode((520, 260))
    pygame.display.set_caption("Slider Demo")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)
    small = pygame.font.SysFont(None, 20)

    slider = Slider(0.3, 0.0, 1.0, speed=10.0)
    bar_rect = pygame.Rect(60, 140, 400, 36)

    running = True
    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                slider.set_value(random.random())   # 走缓动
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
                slider.set_value(random.random(), immediate=True)  # 瞬移对照
            else:
                slider.handle_event(event, bar_rect)

        slider.update(dt)

        screen.fill((28, 28, 34))
        slider.draw(screen, bar_rect)

        txt = font.render(f"{slider.value:.3f}", True, (220, 220, 228))
        screen.blit(txt, (bar_rect.centerx - txt.get_width() // 2, 60))

        hint = small.render(
            "拖动 = 跟手   空格 = set_value() 缓动    回车 = immediate 瞬移",
            True, (140, 140, 152))
        screen.blit(hint, (60, 200))

        pygame.display.flip()

    pygame.quit()