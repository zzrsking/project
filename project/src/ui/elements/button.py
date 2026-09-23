import pygame
import random
import math
"""
工业黄	(255, 200, 0)	经典工业警示黄，明亮醒目，默认风格最协调
暗黄/土黄	(200, 160, 0)	沉稳不刺眼，适合复古工业风
鲜绿	(80, 220, 80)	亮眼活泼，强调“开始/运行”等正向动作
草绿	(80, 180, 80)	柔和自然，长时间注视不疲劳
暗绿	(50, 120, 50)	低调稳重，适合“确认/保存”等严肃操作
橙红	(255, 100, 50)	热情、紧急感，适合“警告/停止”类按钮
亮红	(220, 60, 60)	强烈警示，适合“删除/关闭”等危险操作
天蓝	(0, 180, 255)	清爽科技感，适合“信息/帮助”类按钮
深蓝	(30, 80, 200)	专业冷静，适合“功能/设置”类按钮
紫色	(180, 80, 200)	创意或特殊功能，有区分度
玫红	(220, 80, 160)	鲜明个性，适合“女性/艺术”类主题
rgb(255, 228, 20)
"""

import pygame
import math

from src.ui.elements import theme
from src.ui.elements.fonts import get_font

# ========== 全局参数 ==========
ANIMATION_SPEED = 12.0          # 缓动速度（与 Switch 一致）
HOVER_BRIGHTEN = 0.15           # 悬停时整颗按钮提亮幅度
BORDER_WIDTH = 2                # 边框宽度（原始像素）
UNSELECTED_FILL_ALPHA = 30      # 未选中时白色玻璃的透明度
COLORED_FILL_ALPHA = 120        # 选中/独立时彩色玻璃的透明度（比原来更实一些，让渐变过程看得见）
UNSELECTED_GLASS = (245, 247, 250)  # 未选中时毛玻璃的“低饱和度白”基底色
# 不透明模式（侧栏等实心场景用）：未选中是实心深灰面，选中是实心主题色，不做半透明玻璃
OPAQUE_FILL = (40, 44, 62)
OPAQUE_BORDER = (66, 72, 96)

# ---- 现代扁平风格参数 ----
# 纵深只靠三样很克制的东西：极淡竖向渐变 + 顶部高光 + 底部柔和投影。
# 不做斜面浮雕（那是上世纪的做法）。
CORNER_RADIUS = 5                # 轻微圆角（圆角要抗锯齿，见 BUTTON_AA）
BUTTON_AA = 3                    # 按钮体超采样倍数（投影/描边不够就调这里）
SHADOW_PAD = 6                   # 底部投影预留的边距
# 颜色统一取自 theme（就是游戏原本那套 slate 配色），这里只做别名，别再手写魔法数字
BASE_SURFACE = theme.CARD_BG     # 未选中：统一中性面（组内所有按钮一致，不花哨）
BASE_BORDER = theme.BORDER
BASE_BORDER_HOVER = theme.BORDER_HI
TEXT_IDLE = theme.TEXT_DIM       # 未选中文字（次级）
HOVER_LIGHTEN = 0.10             # 悬停整体提亮
TOP_GRADIENT = 1.09              # 面顶部提亮（很淡，只为一点点纵深）
BOTTOM_GRADIENT = 0.95           # 面底部压暗
SHADOW_RGB = theme.SHADOW        # 投影基色

# ---- 组内选中项的左侧指示条（+ 淡彩光晕） ----
BAR_W = 3                        # 指示条宽度
BAR_INSET = 5                    # 距左边缘
BAR_HEIGHT_RATIO = 0.46          # 占按钮高度的比例
AA = 4                          # 超采样倍数（4倍，可调至 2 或 3 降低开销）
DEFAULT_COLOR = (220, 80, 160)  # 默认主题色（玫红）
white = (255, 255, 255)
# 状态量化档数：缓动连续变化，但外观只需这么多档就看不出来。
# 档位越少 -> 动画期间重建次数越少；32 档在 0.5s 的过渡里每档约 1 帧，肉眼无台阶感。
CACHE_STEPS = 32
# ===============================

class UIButton:
    """现代胶囊按钮，超采样抗锯齿，白色毛玻璃↔主题色玻璃渐变，悬停整颗提亮，与 Switch 视觉统一

    性能说明（本次优化重点）：
      旧实现每帧、每个按钮都要：新建 (w*AA, h*AA) 的 SRCALPHA 大表面 -> 画圆角矩形/描边
      -> 渲染文字 -> `smoothscale` 缩回原尺寸再 blit。180x40 的按钮一帧就是 460KB 的表面
      分配 + 一次平滑缩放；编辑器里 20+ 个按钮（还被画两遍）直接吃掉 15ms/帧。

      现在把"合成结果"缓存起来：只有当按钮的外观状态（尺寸/文字/颜色/进度档位）真的变了
      才重新合成，其余帧只做一次 blit。静止/悬停未变时开销降到微秒级。
    """

    _grad_cache = {}  # 垂直渐变纹理缓存
    _face_cache = {}  # 立体面渐变缓存（按尺寸+两端色）
    _mask_cache = {}  # 圆角掩膜缓存（按尺寸+圆角）

    def __init__(self, rect, text, color=None, group=None, text_color=(255, 255, 255),
                 opaque=False, filled=True):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.text_color = text_color
        self.group = group

        # 不透明模式：实心材质，不做半透明毛玻璃（侧栏这类实心面板上用）
        self.opaque = bool(opaque)
        # 主按钮(filled=True)：选中/独立时填充成 color
        # 次级按钮(filled=False)：永远是中性面 + 描边，只在悬停时出强调色 ——
        # 用来做 "Settings" 这类不需要抢视觉焦点的动作。
        self.filled = bool(filled)

        self.hover = False
        self.selected = False

        self._progress = 0.0          # 选中填充进度 (0~1)
        self._hover_progress = 0.0    # 悬停进度 (0~1)

        self._base_color = color if color is not None else DEFAULT_COLOR
        self._accent_color = self._make_accent_color(self._base_color)

        self._last_time = pygame.time.get_ticks()
        self._cached_texts = {}       # 文字缓存

        # ---- 合成结果缓存 ----
        self._surf = None             # 上一次合成好的按钮表面
        self._surf_key = None         # 对应的外观状态键

        # ===== 新增：独立按钮（无 group）默认选中 =====
        if group is None:
            self.selected = True
            self._progress = 1.0
        # =============================================

        if group is not None:
            group.add_button(self)

    # ========== 颜色接口 ==========
    @property
    def color(self):
        return self._base_color

    @color.setter
    def color(self, new_color):
        self._base_color = new_color
        self._accent_color = self._make_accent_color(new_color)
        self.invalidate()
    # ===============================

    def invalidate(self):
        """丢弃合成缓存（尺寸/文字/颜色被外部改动后调用）。"""
        self._surf = None
        self._surf_key = None

    def set_rect(self, rect):
        """改位置/尺寸。位置不影响缓存（blit 时取 rect.topleft），尺寸会影响。"""
        self.rect = pygame.Rect(rect)
        self.invalidate()

    def check_hover(self, mouse_pos):
        now = pygame.time.get_ticks()
        dt = (now - self._last_time) / 1000.0
        self._last_time = now

        self.hover = self.rect.collidepoint(mouse_pos)

        target_progress = 1.0 if self.selected else 0.0
        self._progress += (target_progress - self._progress) * (1.0 - math.exp(-ANIMATION_SPEED * dt))

        target_hover = 1.0 if self.hover else 0.0
        self._hover_progress += (target_hover - self._hover_progress) * (1.0 - math.exp(-ANIMATION_SPEED * 1.2 * dt))

        # 钳位
        if abs(self._progress - target_progress) < 0.0001:
            self._progress = target_progress
        if abs(self._hover_progress - target_hover) < 0.0001:
            self._hover_progress = target_hover

    def draw(self, surface, size=24):
        """绘制按钮：外观没变就直接复用上次合成好的表面（超采样抗锯齿）。"""
        rect = self.rect
        if rect.width <= 0 or rect.height <= 0:
            return

        key = (rect.width, rect.height, size, self.text, self.text_color,
               self._base_color, self.opaque, self.filled,
               self._step(self._progress), self._step(self._hover_progress))
        if key != self._surf_key:
            self._surf = self._compose(rect.width, rect.height, size)
            self._surf_key = key

        # 合成面上留了 SHADOW_PAD 的投影边距，整体左上偏移回去
        surface.blit(self._surf, (rect.x - SHADOW_PAD, rect.y - SHADOW_PAD))

    @staticmethod
    def _step(value):
        """把 0~1 的连续进度量化成档位（用于缓存键）。"""
        return int(value * CACHE_STEPS + 0.5)

    def _compose(self, width, height, size):
        """现代扁平按钮（轻微圆角 + 克制的纵深）。

        纵深来自三样东西，都很淡：
          · 面色竖向渐变（上 +9% / 下 -5%）
          · 底部柔和投影（悬停/选中时"浮"得高一点，并带上按钮色的淡彩影）
          · 圆角本身

        颜色：未选中 = 统一中性面，选中 = 该按钮自己的 color，中间平滑过渡。
        组内所有按钮共用同一套中性面，所以不会出现"一个按钮一个颜色"的花哨效果。

        圆角需要抗锯齿，所以按钮体按 BUTTON_AA 倍超采样后再缩回；投影是柔和的，
        直接 1x 画，省掉一次超采样。
        """
        pad = SHADOW_PAD
        surf = pygame.Surface((width + pad * 2, height + pad * 2), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        body = pygame.Rect(pad, pad, width, height)

        t = self._progress
        sel = t * t * (3.0 - 2.0 * t)     # smoothstep：选中进度
        # 次级按钮(filled=False)永远不填充强调色，保持中性面
        st = sel if self.filled else 0.0
        hov = self._hover_progress
        alpha = 255 if self.opaque else 228
        in_group = self.group is not None

        # ---- 底色：中性面 <-> 按钮色 ----
        fill = self._mix(BASE_SURFACE, self._base_color, st)
        fill = self._shade(fill, 1.0 + HOVER_LIGHTEN * hov)

        border = self._mix(BASE_BORDER, self._shade(self._base_color, 1.35), st)
        # 次级按钮：悬停时描边转向强调色，给一点反馈但不喧宾夺主
        if st < 0.5:
            border = self._mix(border,
                               self._shade(self._base_color, 1.25) if not self.filled
                               else BASE_BORDER_HOVER,
                               hov)
        else:
            border = self._shade(border, 1.0 + 0.22 * hov)

        # ---- 底部柔和投影：选中项更"浮"，并带按钮色的彩影（淡彩光晕） ----
        lift = 0.35 + 0.45 * hov + 0.30 * sel
        glow = 1.0 + 0.9 * sel
        shadow_rgb = self._mix(SHADOW_RGB, self._base_color, sel * 0.75)
        for i in range(pad, 0, -1):
            a = int(58 * lift * glow * (1.0 - (i - 1) / pad) ** 1.5)
            if a <= 0:
                continue
            r = pygame.Rect(body.x - i + 1, body.y + i,
                            body.w + 2 * i - 2, body.h + i - 1)
            pygame.draw.rect(surf, (*shadow_rgb, min(255, a)), r,
                             border_radius=CORNER_RADIUS + i)

        # ---- 按钮体：超采样 + 圆角裁剪 ----
        aa = BUTTON_AA
        bw, bh = width * aa, height * aa
        radius = CORNER_RADIUS * aa
        big = pygame.Surface((bw, bh), pygame.SRCALPHA)

        # 渐变面 -> 乘圆角掩膜（只保留圆角内的像素，边缘带抗锯齿 alpha）
        grad = self._vertical_face(
            bw, bh,
            (*self._shade(fill, TOP_GRADIENT), alpha),
            (*self._shade(fill, BOTTOM_GRADIENT), alpha)).copy()
        grad.blit(self._rounded_mask(bw, bh, radius), (0, 0),
                  special_flags=pygame.BLEND_RGBA_MULT)
        big.blit(grad, (0, 0))

        # ---- 组内选中项：左侧指示条 + 淡彩光晕 ----
        if in_group and sel > 0.02:
            bar_w = max(1, BAR_W * aa)
            bar_h = max(2, int(bh * BAR_HEIGHT_RATIO))
            bar_x = BAR_INSET * aa
            bar_y = (bh - bar_h) // 2
            for k in (2, 1):
                ga = int(64 * sel / k)
                if ga > 0:
                    grow = k * aa
                    pygame.draw.rect(
                        big, (*self._base_color, ga),
                        (bar_x - grow, bar_y - grow,
                         bar_w + grow * 2, bar_h + grow * 2),
                        border_radius=(bar_w + grow * 2) // 2)
            pygame.draw.rect(
                big, (*self._shade(self._base_color, 1.6), int(255 * sel)),
                (bar_x, bar_y, bar_w, bar_h), border_radius=bar_w // 2)

        # 描边
        pygame.draw.rect(big, (*border, alpha), (0, 0, bw, bh),
                         width=max(1, aa), border_radius=radius)

        # 文字（在超采样尺寸上渲染，缩回后自带抗锯齿）
        text_surf = self._render_text(
            self.text, size * aa, self._mix(TEXT_IDLE, self.text_color, st))
        big.blit(text_surf, text_surf.get_rect(center=(bw // 2, bh // 2)))

        surf.blit(pygame.transform.smoothscale(big, (width, height)), body.topleft)
        return surf

    @classmethod
    def _rounded_mask(cls, w, h, radius):
        """圆角掩膜（白=保留）。按尺寸缓存，别每次重画。"""
        key = (w, h, radius)
        mask = cls._mask_cache.get(key)
        if mask is None:
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                             border_radius=radius)
            if len(cls._mask_cache) >= 128:
                cls._mask_cache.clear()
            cls._mask_cache[key] = mask
        return mask

    @staticmethod
    def _shade(color, k):
        """按系数提亮/压暗一个颜色（k=1 原色）。"""
        return tuple(min(255, max(0, int(c * k))) for c in color[:3])

    @staticmethod
    def _mix(c1, c2, t):
        """两个颜色之间线性插值。"""
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    @classmethod
    def _vertical_face(cls, w, h, top, bot):
        """竖直渐变面：2 像素种子 + smoothscale，插值全在 C 侧。"""
        key = (w, h, top, bot)
        surf = cls._face_cache.get(key)
        if surf is None:
            seed = pygame.Surface((1, 2), pygame.SRCALPHA)
            seed.set_at((0, 0), top)
            seed.set_at((0, 1), bot)
            surf = pygame.transform.smoothscale(seed, (max(1, w), max(1, h)))
            if len(cls._face_cache) >= 256:
                cls._face_cache.clear()
            cls._face_cache[key] = surf
        return surf

    def _render_text(self, text, size, color):
        key = (text, size, color)
        if key not in self._cached_texts:
            font = get_font(size)
            self._cached_texts[key] = font.render(text, True, color)
            if len(self._cached_texts) > 8:       # 文字变了也不会无限长胖
                for k in list(self._cached_texts)[:-4]:
                    del self._cached_texts[k]
        return self._cached_texts[key]

    def _make_accent_color(self, color):
        """生成更亮、饱和度稍高的强调色（用于内部增强）"""
        r, g, b = color[:3]
        h, s, v = self._rgb_to_hsv(r, g, b)
        s = min(1.0, s * 1.2)
        v = min(1.0, v * 1.2)
        return self._hsv_to_rgb(h, s, v)

    # ---- HSV 转换 ----
    @staticmethod
    def _rgb_to_hsv(r, g, b):
        r, g, b = r/255.0, g/255.0, b/255.0
        mx = max(r, g, b)
        mn = min(r, g, b)
        df = mx - mn
        if mx == mn:
            h = 0
        elif mx == r:
            h = (60 * ((g - b) / df) + 360) % 360
        elif mx == g:
            h = (60 * ((b - r) / df) + 120) % 360
        elif mx == b:
            h = (60 * ((r - g) / df) + 240) % 360
        s = 0 if mx == 0 else df / mx
        v = mx
        return h, s, v

    @staticmethod
    def _hsv_to_rgb(h, s, v):
        h = h % 360
        c = v * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = v - c
        if 0 <= h < 60:
            r, g, b = c, x, 0
        elif 60 <= h < 120:
            r, g, b = x, c, 0
        elif 120 <= h < 180:
            r, g, b = 0, c, x
        elif 180 <= h < 240:
            r, g, b = 0, x, c
        elif 240 <= h < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)

    # ---- 渐变缓存（与 Switch 完全一致） ----
    @classmethod
    def _get_cached_gradient(cls, height, color_top, color_bottom):
        key = (height, color_top, color_bottom)
        if key not in cls._grad_cache:
            cls._grad_cache[key] = cls._make_vertical_gradient(height, color_top, color_bottom)
        return cls._grad_cache[key]

    @staticmethod
    def _make_vertical_gradient(height, color_top, color_bottom):
        surf = pygame.Surface((1, height), pygame.SRCALPHA)
        for y in range(height):
            t = y / (height - 1) if height > 1 else 0
            r = int(color_top[0] + (color_bottom[0] - color_top[0]) * t)
            g = int(color_top[1] + (color_bottom[1] - color_top[1]) * t)
            b = int(color_top[2] + (color_bottom[2] - color_top[2]) * t)
            a = int(color_top[3] + (color_bottom[3] - color_top[3]) * t)
            surf.set_at((0, y), (r, g, b, a))
        return surf


# ========== ButtonGroup 类（完全未改动） ==========
class ButtonGroup:
    def __init__(self, buttons=None):
        self._buttons = []
        self.selected_index = -1
        if buttons:
            for btn in buttons:
                self.add_button(btn)

    def __len__(self):
        return len(self._buttons)

    def __getitem__(self, index):
        return self._buttons[index]

    def __setitem__(self, index, button):
        if not isinstance(button, UIButton):
            raise TypeError("只能添加 UIButton 实例")
        if self._buttons[index] in self._buttons:
            self._buttons[index].group = None
        self._buttons[index] = button
        button.group = self

    def __delitem__(self, index):
        if self._buttons[index] == self.get_selected():
            self.selected_index = -1
        self._buttons[index].group = None
        del self._buttons[index]

    def __iter__(self):
        return iter(self._buttons)

    def __contains__(self, button):
        return button in self._buttons

    def __reversed__(self):
        return reversed(self._buttons)

    def __add__(self, other):
        if isinstance(other, ButtonGroup):
            new_group = ButtonGroup()
            for btn in self._buttons:
                new_group._adopt(btn)
            for btn in other._buttons:
                new_group._adopt(btn)
            new_group._normalize_selection()
            return new_group
        elif isinstance(other, UIButton):
            new_group = ButtonGroup()
            for btn in self._buttons:
                new_group._adopt(btn)
            new_group._adopt(other)
            new_group._normalize_selection()
            return new_group
        elif isinstance(other, list):
            new_group = ButtonGroup()
            for btn in self._buttons:
                new_group._adopt(btn)
            for btn in other:
                if isinstance(btn, UIButton):
                    new_group._adopt(btn)
            new_group._normalize_selection()
            return new_group
        else:
            raise TypeError(f"不支持的类型: {type(other)}")

    def __radd__(self, other):
        if isinstance(other, UIButton):
            new_group = ButtonGroup()
            new_group._adopt(other)
            for btn in self._buttons:
                new_group._adopt(btn)
            new_group._normalize_selection()
            return new_group
        elif isinstance(other, list):
            new_group = ButtonGroup()
            for btn in other:
                if isinstance(btn, UIButton):
                    new_group._adopt(btn)
            for btn in self._buttons:
                new_group._adopt(btn)
            new_group._normalize_selection()
            return new_group
        else:
            raise TypeError(f"不支持的类型: {type(other)}")

    def __iadd__(self, other):
        if isinstance(other, ButtonGroup):
            for btn in other._buttons:
                self._adopt(btn)
            self._normalize_selection()
        elif isinstance(other, UIButton):
            self._adopt(other)
            self._normalize_selection()
        elif isinstance(other, list):
            for btn in other:
                if isinstance(btn, UIButton):
                    self._adopt(btn)
            self._normalize_selection()
        else:
            raise TypeError(f"不支持的类型: {type(other)}")
        return self

    def append(self, button):
        self.add_button(button)

    def insert(self, index, button):
        if not isinstance(button, UIButton):
            raise TypeError("只能添加 UIButton 实例")
        self._buttons.insert(index, button)
        button.group = self

    def remove(self, button):
        self.remove_button(button)

    def pop(self, index=-1):
        button = self._buttons.pop(index)
        if button == self.get_selected():
            self.selected_index = -1
        button.group = None
        return button

    def index(self, button):
        return self._buttons.index(button)

    def clear(self):
        for btn in self._buttons:
            btn.group = None
            btn.selected = False
        self._buttons.clear()
        self.selected_index = -1

    def add_button(self, button):
        if not isinstance(button, UIButton):
            raise TypeError("只能添加 UIButton 实例")
        if button not in self._buttons:
            # 加入 group 的按钮一律从“未选中”开始
            button.selected = False
            button._progress = 0.0
            button.group = self
            self._buttons.append(button)
            # 若组里没有任何选中项，则默认选中第一个按钮
            if self.selected_index == -1:
                self.select(self._buttons[0])

    def _adopt(self, button):
        """把已构造好的按钮纳入本组，但不重置其 selected/_progress（非破坏性）。

        仅用于 __add__/__radd__/__iadd__ 等合并运算符：合并分组只是逻辑上的组合，
        不应把每个按钮的选中/动画进度清空，否则点击后的高光/渐变/动画会失效。
        """
        if button not in self._buttons:
            button.group = self
            self._buttons.append(button)

    def _normalize_selection(self):
        """保证组内只有一个选中项：保留第一个被选中的按钮，否则选中第一个按钮。"""
        first_selected = next((b for b in self._buttons if b.selected), None)
        if first_selected is not None:
            self.select(first_selected)
        elif self._buttons:
            self.select(self._buttons[0])

    def remove_button(self, button):
        if button in self._buttons:
            if button == self.get_selected():
                self.selected_index = -1
            self._buttons.remove(button)
            button.group = None

    def select(self, button):
        if button not in self._buttons:
            return
        if button == self.get_selected():
            return

        for btn in self._buttons:
            if btn.selected:
                btn.selected = False

        button.selected = True
        self.selected_index = self._buttons.index(button)

    def select_by_index(self, index):
        if 0 <= index < len(self._buttons):
            self.select(self._buttons[index])

    def select_by_text(self, text):
        for button in self._buttons:
            if button.text == text:
                self.select(button)

    def get_selected(self):
        if self.selected_index >= 0 and self.selected_index < len(self._buttons):
            return self._buttons[self.selected_index]
        return None

    def get_selected_index(self):
        return self.selected_index

    def clear_selection(self):
        for btn in self._buttons:
            btn.selected = False
        self.selected_index = -1

    @property
    def count(self):
        return len(self._buttons)

    @property
    def buttons(self):
        return self._buttons.copy()

    def find_button(self, index=None, text=None):
        if text is not None:
            for btn in self._buttons:
                if btn.text == text:
                    return btn
        elif index is not None:
            for btn in self._buttons:
                if btn.text == index:
                    return btn
        return None