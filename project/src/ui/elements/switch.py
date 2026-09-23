import pygame
import math

# 状态量化档数（同 UIButton）：缓动连续变化，外观只需这么多档。
# 档位越少 -> 动画期间重建越少；48 档在滑块 0.4s 的滑动里每档约半帧，肉眼看不出台阶。
CACHE_STEPS = 48


class Switch:
    # 类级缓存，所有实例共享，按 (高度, 颜色元组) 索引
    _grad_cache = {}

    def __init__(self, bool_var, speed=12.0):
        self._state = bool(bool_var)
        self._target = self._state
        self._pos = 1.0 if self._state else 0.0
        self._hover = False
        self._hover_progress = 0.0
        self._speed = speed

        # ---- 合成结果缓存（外观没变就只 blit，不再每帧建表面 + smoothscale） ----
        self._surf = None
        self._surf_key = None

    def switch(self):
        self.set_bool(not self._state)

    def set_bool(self, bool_var):
        bool_var = bool(bool_var)
        if self._state != bool_var:
            self._state = bool_var
            self._target = bool_var

    @property
    def on(self):
        self.set_bool(True)
        return self._state

    @property
    def off(self):
        self.set_bool(False)
        return self._state

    def __bool__(self):
        return self._state

    def set_hover(self, hover):
        self._hover = bool(hover)

    def update(self, dt):
        target_pos = 1.0 if self._target else 0.0
        diff = target_pos - self._pos
        if abs(diff) > 0.0001:
            self._pos += diff * (1.0 - math.exp(-self._speed * dt))
            if abs(self._pos - target_pos) < 0.0001:
                self._pos = target_pos
        else:
            self._pos = target_pos

        target_hover = 1.0 if self._hover else 0.0
        diff_h = target_hover - self._hover_progress
        if abs(diff_h) > 0.0001:
            self._hover_progress += diff_h * (1.0 - math.exp(-self._speed * 1.2 * dt))
            if abs(self._hover_progress - target_hover) < 0.0001:
                self._hover_progress = target_hover
        else:
            self._hover_progress = target_hover

    @staticmethod
    def _step(value):
        return int(value * CACHE_STEPS + 0.5)

    def invalidate(self):
        self._surf = None
        self._surf_key = None

    def draw(self, surface, rect,
             bg_off=(80, 80, 90),
             bg_on=(0, 180, 80),
             knob_color=(230, 230, 230),
             knob_hover_color=(255, 255, 255),
             border_radius=None):
        """绘制开关：外观状态未变时直接复用上一次合成好的表面。"""
        if rect.width <= 0 or rect.height <= 0:
            return
        key = (rect.width, rect.height, border_radius, tuple(bg_off), tuple(bg_on),
               tuple(knob_color), tuple(knob_hover_color),
               self._step(self._pos), self._step(self._hover_progress))
        if key != self._surf_key:
            self._surf = self._compose(rect.width, rect.height, bg_off, bg_on,
                                       knob_color, knob_hover_color, border_radius)
            self._surf_key = key
        surface.blit(self._surf, (int(rect.x), int(rect.y)))

    def _compose(self, width, height, bg_off, bg_on, knob_color, knob_hover_color,
                 border_radius):
        """合成整个开关（滑轨 + 滑块）到一张 rect 大小的表面（局部坐标）。"""
        # ---------- 超采样降至 4 倍 ----------
        AA = 4

        # ---------- 背景（滑轨） ----------
        bg = self._lerp_color(bg_off, bg_on, self._pos)
        if self._hover_progress > 0:
            bright_factor = 1.0 + self._hover_progress * 0.15
            bg = tuple(min(255, int(c * bright_factor)) for c in bg)

        radius = height // 2 if border_radius is None else border_radius

        bg_w = int(width * AA)
        bg_h = int(height * AA)
        bg_surf = pygame.Surface((bg_w, bg_h), pygame.SRCALPHA)
        bg_surf.fill((0, 0, 0, 0))
        pygame.draw.rect(bg_surf, bg,
                         (0, 0, bg_w, bg_h),
                         border_radius=int(radius * AA))

        # ---- 背景立体渐变（使用缓存 + 快速拉伸） ----
        bg_grad = self._get_cached_gradient(bg_h, (255, 255, 255, 255), (220, 220, 220, 255))
        bg_grad_scaled = pygame.transform.scale(bg_grad, (bg_w, bg_h))
        bg_surf.blit(bg_grad_scaled, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # ---------- 整张表面（局部坐标 0,0 起） ----------
        composed = pygame.transform.smoothscale(bg_surf, (int(width), int(height)))

        # ---------- 滑块 ----------
        pad = 6
        base_height = height - 2 * pad
        if base_height < 4:
            base_height = 4

        min_ratio = 0.55
        max_ratio = 1.0
        width_ratio = min_ratio + (max_ratio - min_ratio) * self._hover_progress
        base_width = base_height * width_ratio

        hover_scale = 1.0 + 0.08 * self._hover_progress
        final_width = base_width * hover_scale
        final_height = base_height * hover_scale
        final_width = max(final_width, 2.0)
        final_height = max(final_height, 2.0)

        track_width = width - 2 * pad - final_width
        x = pad + self._pos * track_width
        y = (height - final_height) / 2

        knob_w = int(final_width * AA)
        knob_h = int(final_height * AA)
        knob_surf = pygame.Surface((knob_w, knob_h), pygame.SRCALPHA)
        knob_surf.fill((0, 0, 0, 0))
        r = min(knob_w, knob_h) // 2
        r = max(r, 1)

        knob_color_mixed = self._lerp_color(knob_color, knob_hover_color, self._hover_progress)
        pygame.draw.rect(knob_surf, knob_color_mixed, (0, 0, knob_w, knob_h), border_radius=r)

        # ---- 滑块立体渐变（缓存 + 快速拉伸） ----
        # 乘法（阴影）：顶部亮 底部暗，alpha=255 不影响透明度
        mult_grad = self._get_cached_gradient(knob_h, (255, 255, 255, 255), (180, 180, 180, 255))
        mult_grad_scaled = pygame.transform.scale(mult_grad, (knob_w, knob_h))
        knob_surf.blit(mult_grad_scaled, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # 加法（高光）：alpha=0，只加颜色不改变透明度，消除“矩形膜”
        add_grad = self._get_cached_gradient(knob_h, (255, 255, 255, 0), (0, 0, 0, 0))
        add_grad_scaled = pygame.transform.scale(add_grad, (knob_w, knob_h))
        knob_surf.blit(add_grad_scaled, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        knob_scaled = pygame.transform.smoothscale(knob_surf, (int(final_width), int(final_height)))
        composed.blit(knob_scaled, (int(x), int(y)))
        return composed

    @staticmethod
    def _lerp_color(c1, c2, t):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    @classmethod
    def _get_cached_gradient(cls, height, color_top, color_bottom):
        """获取缓存的 1px 宽垂直渐变纹理，避免每帧重复生成"""
        key = (height, color_top, color_bottom)
        if key not in cls._grad_cache:
            cls._grad_cache[key] = cls._make_vertical_gradient(height, color_top, color_bottom)
        return cls._grad_cache[key]

    @staticmethod
    def _make_vertical_gradient(height, color_top, color_bottom):
        """生成 1px 宽垂直渐变（含 alpha）"""
        surf = pygame.Surface((1, height), pygame.SRCALPHA)
        for y in range(height):
            t = y / (height - 1) if height > 1 else 0
            r = int(color_top[0] + (color_bottom[0] - color_top[0]) * t)
            g = int(color_top[1] + (color_bottom[1] - color_top[1]) * t)
            b = int(color_top[2] + (color_bottom[2] - color_top[2]) * t)
            a = int(color_top[3] + (color_bottom[3] - color_top[3]) * t) if len(color_top) > 3 else 255
            surf.set_at((0, y), (r, g, b, a))
        return surf
