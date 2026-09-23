"""现代风格文本输入框组件。

设计参照 switch.py：
- 外观用 (尺寸, 颜色, 量化状态) 做 key 缓存，未变化时只 blit；
- 超采样 + smoothscale 得到抗锯齿圆角与聚焦外发光；
- 文本走 fonts.get_font 的字体缓存，避免每帧解析字体文件。

功能：
- 光标 / 选区 / 拖拽选择、Ctrl+A/C/X/V、Home/End、Ctrl+方向键按词移动；
- 长按方向键 / Backspace / Delete 连续重复（自实现，不依赖 SDL 的 set_repeat）；
- 中文 / 日文 / 韩文 IME 输入（TEXTINPUT 事件）；
- IME 候选窗口跟随光标位置（set_text_input_rect）；
- 水平滚动跟随光标（带缓动）、光标平滑闪烁；
- placeholder、密码模式（用 * 掩码）、最大长度、回车提交回调；
- 焦点组：同组 InputBox 互斥聚焦，聚焦一个会自动让其他失焦。
"""
import math
import pygame

try:
    from .ime import ImeCursor, start as ime_start, stop as ime_stop
except ImportError:  # 单独跑这个文件时的兜底
    from ime import ImeCursor, start as ime_start, stop as ime_stop

try:
    from fonts import get_font, DEFAULT_FONT_PATH
except ImportError:  # 单独跑这个文件时的兜底
    DEFAULT_FONT_PATH = None
    _FALLBACK_FONTS = {}

    def get_font(size, path=None):
        key = (path, int(size))
        font = _FALLBACK_FONTS.get(key)
        if font is None:
            try:
                font = pygame.font.Font(path, int(size))
            except Exception:
                font = pygame.font.Font(None, int(size))
            _FALLBACK_FONTS[key] = font
        return font


# 状态量化档数（同 UIButton / Switch）：缓动连续变化，外观只需这么多档。
CACHE_STEPS = 48

# 聚焦外发光向外预留的像素，blit 时整体左移/上移这么多。
GLOW = 6

# 超采样倍数（圆角 / 边框抗锯齿）
AA = 3

# 按键重复：自实现，SDL2 默认不开 set_repeat。
REPEAT_DELAY = 0.40      # 按住多久后开始重复
REPEAT_INTERVAL = 0.04   # 之后每隔多久重复一次
_REPEATABLE_KEYS = frozenset({
    pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
    pygame.K_BACKSPACE, pygame.K_DELETE,
})

# [IME] 光标跟踪器：位置变化时才通知 SDL，避免每帧重复调 API
# （SDL 是否支持 set_text_input_rect 由 ime 模块统一判断）


# --------------------------------------------------------------------------- #
# 剪贴板（tkinter 可选依赖，拿不到就静默降级）
# --------------------------------------------------------------------------- #
_TK_ROOT = None
_TK_FAILED = False


def _tk_root():
    global _TK_ROOT, _TK_FAILED
    if _TK_ROOT is None and not _TK_FAILED:
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            _TK_ROOT = root
        except Exception:
            _TK_FAILED = True
    return _TK_ROOT


def get_clipboard_text():
    root = _tk_root()
    if root is None:
        return ""
    try:
        return root.clipboard_get()
    except Exception:
        return ""


def set_clipboard_text(text):
    root = _tk_root()
    if root is None:
        return
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _approach(current, target, dt, speed):
    diff = target - current
    if abs(diff) > 0.0001:
        current += diff * (1.0 - math.exp(-speed * dt))
        if abs(current - target) < 0.0001:
            current = target
    else:
        current = target
    return current


# --------------------------------------------------------------------------- #
# InputBox
# --------------------------------------------------------------------------- #
class InputBox:
    def __init__(self, text="", placeholder="", *,
                 max_length=None,
                 password=False,
                 font_size=22,
                 font_path=DEFAULT_FONT_PATH,
                 speed=14.0,
                 blink_interval=0.53,
                 on_change=None,
                 on_submit=None,
                 focus_group=None):
        """
        :param text:          初始文本
        :param placeholder:   空文本时显示的灰色提示
        :param max_length:    最大字符数，None 不限
        :param password:      True 时用 * 代替显示
        :param font_size:     字号（走 fonts.get_font 缓存）
        :param font_path:     字体路径，None 表示 pygame 内置字体
        :param speed:         缓动速度
        :param blink_interval: 光标闪烁半周期（秒）
        :param on_change:     回调 (text) -> None
        :param on_submit:     回调 (text) -> None，回车触发
        :param focus_group:   共享的 list，同组 InputBox 自动互斥聚焦。
                              也可事后用 InputBox.link([...]) 建立。
        """
        self._text = str(text)
        self._cursor = len(self._text)
        self._anchor = self._cursor          # 选区锚点
        self._max_length = max_length
        self._password = bool(password)
        self._placeholder = placeholder
        self._font_size = max(1, int(font_size))
        self._font_path = font_path
        self._speed = speed
        self._blink_interval = blink_interval
        self._on_change = on_change
        self._on_submit = on_submit

        self._focused = False
        self._focus_progress = 0.0
        self._hover = False
        self._hover_progress = 0.0
        self._auto_hover = True
        self._dragging = False

        self._scroll = 0.0
        self._scroll_target = 0.0
        self._blink_t = 0.0

        # 按键重复状态：{keycode: 距下次重复的秒数}
        self._held_keys = {}

        # 焦点组：共享 list，或 None 表示独立
        self._focus_group = focus_group
        if focus_group is not None:
            focus_group.append(self)

        # [IME] 输入法候选框跟随光标的跟踪器
        self._ime = ImeCursor()

        # 由 draw() 更新，用于鼠标命中测试
        self._rect = pygame.Rect(0, 0, 0, 0)

        # ---- 合成结果缓存 ----
        self._surf = None
        self._surf_key = None

    # ------------------------------------------------------------------ #
    # 焦点组
    # ------------------------------------------------------------------ #
    @staticmethod
    def link(boxes):
        """让一组 InputBox 互斥聚焦，返回原列表方便链式书写。"""
        group = list(boxes)
        for b in group:
            b._focus_group = group
        return boxes

    # ------------------------------------------------------------------ #
    # 基础属性
    # ------------------------------------------------------------------ #
    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        value = "" if value is None else str(value)
        if self._max_length is not None:
            value = value[:self._max_length]
        if value != self._text:
            self._text = value
            self._cursor = len(value)
            self._anchor = self._cursor
            self._notify_change()

    @property
    def focused(self):
        return self._focused

    @property
    def rect(self):
        return self._rect.copy()

    def __bool__(self):
        return bool(self._text)

    def __str__(self):
        return self._text

    def clear(self):
        self.text = ""

    def select_all(self):
        self._anchor = 0
        self._cursor = len(self._text)
        self._blink_t = 0.0

    def focus(self):
        if self._focused:
            return
        # 同组互斥：先让其他框失焦
        if self._focus_group is not None:
            for other in self._focus_group:
                if other is not self and other._focused:
                    other.blur()
        self._focused = True
        self._blink_t = 0.0
        # [IME] 强制下一次 draw 重新通知 SDL 输入矩形；并激活输入法
        self._ime.invalidate()
        ime_start()

    def blur(self):
        if not self._focused:
            self._dragging = False
            return
        self._focused = False
        self._dragging = False
        self._held_keys.clear()
        self._ime.invalidate()
        ime_stop()

    def set_hover(self, hover):
        self._auto_hover = False
        self._hover = bool(hover)

    def invalidate(self):
        self._surf = None
        self._surf_key = None

    # ------------------------------------------------------------------ #
    # 更新
    # ------------------------------------------------------------------ #
    def update(self, dt):
        self._focus_progress = _approach(
            self._focus_progress, 1.0 if self._focused else 0.0, dt, self._speed)

        hover_target = 1.0 if (self._hover and not self._focused) else 0.0
        self._hover_progress = _approach(
            self._hover_progress, hover_target, dt, self._speed * 1.2)

        self._scroll = _approach(
            self._scroll, self._scroll_target, dt, self._speed * 1.5)

        if self._focused:
            self._blink_t += dt

        # 长按连续重复
        if self._focused and self._held_keys:
            mods = pygame.key.get_mods()
            for k in list(self._held_keys.keys()):
                self._held_keys[k] -= dt
                if self._held_keys[k] <= 0:
                    self._apply_key(k, mods)
                    self._held_keys[k] = REPEAT_INTERVAL

        if self._auto_hover and self._rect.width > 0 and not self._dragging:
            self._hover = self._rect.collidepoint(pygame.mouse.get_pos())

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def handle_event(self, event):
        """处理事件；返回 True 表示已消费。

        注意：**一个事件可能被多个框依次调用**，不要因为某个框返回 True
        就跳过其余框——否则点击新框时旧框不会失焦。焦点组会自动兜底，
        但推荐仍然把事件分发到所有框。
        """
        # ---------------- 鼠标 ----------------
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._rect.width and self._rect.collidepoint(event.pos):
                self.focus()
                self._dragging = True
                idx = self._index_from_x(event.pos[0])
                self._cursor = idx
                self._anchor = idx
                self._ime.invalidate()          # [IME] 光标变了，位置需要刷新
                return True
            # 点到框外：失焦
            if self._focused:
                self.blur()
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
            return False

        if event.type == pygame.MOUSEMOTION:
            if self._dragging:
                if event.buttons[0]:
                    self._cursor = self._index_from_x(event.pos[0])
                    self._blink_t = 0.0
                    self._ime.invalidate()  # [IME]
                    return True
                self._dragging = False
            return False

        # ---------------- 按键抬起：清理重复状态 ----------------
        if event.type == pygame.KEYUP:
            self._held_keys.pop(event.key, None)
            return False

        # ---------------- 文本输入（含 IME / 中文） ----------------
        if event.type == pygame.TEXTINPUT:
            if self._focused:
                self._insert(event.text)
                return True
            return False

        # ---------------- 键盘 ----------------
        if event.type != pygame.KEYDOWN or not self._focused:
            return False

        key = event.key
        mods = event.mod
        ctrl = bool(mods & pygame.KMOD_CTRL)

        # Tab 交给上层做焦点切换
        if key == pygame.K_TAB:
            return False

        # 提交
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self._on_submit:
                self._on_submit(self._text)
            return True

        # 失焦
        if key == pygame.K_ESCAPE:
            self.blur()
            return True

        # 可重复的键：记下来交给 update() 处理
        if key in _REPEATABLE_KEYS:
            if key in self._held_keys:
                return True  # 忽略 SDL 可能重复发来的 KEYDOWN
            self._held_keys[key] = REPEAT_DELAY
            return self._apply_key(key, mods)

        # 剪贴板
        if ctrl and key == pygame.K_a:
            self.select_all()
            return True

        if ctrl and key == pygame.K_c:
            a, b = self._sel_range()
            if a != b:
                set_clipboard_text(self._text[a:b])
            return True

        if ctrl and key == pygame.K_x:
            a, b = self._sel_range()
            if a != b:
                set_clipboard_text(self._text[a:b])
                self._delete_selection()
            return True

        if ctrl and key == pygame.K_v:
            self._insert(get_clipboard_text())
            return True

        return False

    # ------------------------------------------------------------------ #
    # 按键动作，handle_event 与重复逻辑共用
    # ------------------------------------------------------------------ #
    def _apply_key(self, key, mods):
        shift = bool(mods & pygame.KMOD_SHIFT)
        ctrl = bool(mods & pygame.KMOD_CTRL)

        # [IME] 光标位置可能变化，下一次 draw 重新通知 SDL
        self._ime.invalidate()

        if key == pygame.K_BACKSPACE:
            if not self._delete_selection():
                if self._cursor > 0:
                    self._text = (self._text[:self._cursor - 1]
                                  + self._text[self._cursor:])
                    self._cursor -= 1
                    self._anchor = self._cursor
                    self._notify_change()
            self._blink_t = 0.0
            return True

        if key == pygame.K_DELETE:
            if not self._delete_selection():
                if self._cursor < len(self._text):
                    self._text = (self._text[:self._cursor]
                                  + self._text[self._cursor + 1:])
                    self._notify_change()
            self._blink_t = 0.0
            return True

        if key in (pygame.K_LEFT, pygame.K_RIGHT):
            step = -1 if key == pygame.K_LEFT else 1
            new = self._word_move(step) if ctrl else self._cursor + step
            new = max(0, min(len(self._text), new))
            self._cursor = new
            if not shift:
                self._anchor = new
            self._blink_t = 0.0
            return True

        if key == pygame.K_HOME:
            self._cursor = 0
            if not shift:
                self._anchor = 0
            self._blink_t = 0.0
            return True

        if key == pygame.K_END:
            self._cursor = len(self._text)
            if not shift:
                self._anchor = self._cursor
            self._blink_t = 0.0
            return True

        return False

    # ------------------------------------------------------------------ #
    # 文本编辑内部实现
    # ------------------------------------------------------------------ #
    def _display_text(self):
        # [PWD] 密码模式用 * 掩码
        if self._password:
            return "*" * len(self._text)
        return self._text

    def _sel_range(self):
        if self._cursor < self._anchor:
            return self._cursor, self._anchor
        return self._anchor, self._cursor

    def _notify_change(self):
        self._blink_t = 0.0
        if self._on_change:
            self._on_change(self._text)

    def _delete_selection(self, notify=True):
        a, b = self._sel_range()
        if a == b:
            return False
        self._text = self._text[:a] + self._text[b:]
        self._cursor = a
        self._anchor = a
        if notify:
            self._notify_change()
        return True

    def _insert(self, s):
        if not s:
            return
        self._delete_selection(notify=False)
        if self._max_length is not None:
            room = self._max_length - len(self._text)
            if room <= 0:
                return
            s = s[:room]
        if not s:
            return
        c = self._cursor
        self._text = self._text[:c] + s + self._text[c:]
        self._cursor = c + len(s)
        self._anchor = self._cursor
        self._ime.invalidate()          # [IME] 光标右移
        self._notify_change()

    def _word_move(self, direction):
        text = self._text
        n = len(text)
        i = self._cursor
        if direction < 0:
            while i > 0 and text[i - 1].isspace():
                i -= 1
            while i > 0 and not text[i - 1].isspace():
                i -= 1
        else:
            while i < n and not text[i].isspace():
                i += 1
            while i < n and text[i].isspace():
                i += 1
        return i

    def _index_from_x(self, x):
        """把屏幕 x 坐标换算成文本索引。"""
        font = get_font(self._font_size, self._font_path)
        display = self._display_text()
        pad = self._pad_x(self._rect.height) if self._rect.height else 0
        local = x - self._rect.x - pad + self._scroll
        if local <= 0:
            return 0
        acc = 0.0
        for i, ch in enumerate(display):
            w = font.size(ch)[0]
            if local < acc + w * 0.5:
                return i
            acc += w
        return len(display)

    @staticmethod
    def _pad_x(height):
        return max(8, min(18, int(height * 0.3)))

    @staticmethod
    def _step(value):
        v = int(value * CACHE_STEPS + 0.5)
        return 0 if v < 0 else (CACHE_STEPS if v > CACHE_STEPS else v)

    # ------------------------------------------------------------------ #
    # [IME] 通知 SDL 当前光标位置，让输入法候选框跟随
    # ------------------------------------------------------------------ #
    def _update_ime_rect(self, rect, pad, cursor_px):
        if not self._focused:
            return
        # 光标在窗口坐标系中的位置（1px 宽、与输入框等高）
        cx = rect.x + pad + cursor_px - self._scroll
        cy = rect.y
        self._ime.follow(cx, cy, w=1, h=rect.height)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def draw(self, surface, rect,
             bg_color=(38, 40, 48),
             bg_focus_color=(45, 48, 60),
             border_color=(72, 76, 90),
             border_focus_color=(0, 168, 224),
             text_color=(232, 236, 244),
             placeholder_color=(112, 118, 134),
             selection_color=(0, 168, 224, 90),
             cursor_color=(0, 190, 240),
             border_radius=None):
        """绘制输入框：外观状态未变时直接复用上一次合成好的表面。"""
        rect = pygame.Rect(rect)
        self._rect = rect.copy()
        if rect.width <= 0 or rect.height <= 0:
            return

        font = get_font(self._font_size, self._font_path)
        display = self._display_text()
        pad = self._pad_x(rect.height)
        content_w = max(1, rect.width - 2 * pad)

        # ---- 水平滚动：保证光标可见 ----
        cursor_px = font.size(display[:self._cursor])[0]
        total_px = font.size(display)[0]
        max_scroll = max(0.0, float(total_px + 2 - content_w))

        target = self._scroll_target
        if cursor_px - self._scroll > content_w:
            target = cursor_px - content_w
        elif cursor_px - self._scroll < 0.0:
            target = cursor_px
        self._scroll_target = max(0.0, min(float(target), max_scroll))
        if self._scroll > max_scroll:
            self._scroll = max_scroll

        # [IME] 让输入法候选窗口跟随光标
        self._update_ime_rect(rect, pad, cursor_px)

        # ---- 光标闪烁（末段淡出，比硬闪更现代） ----
        if self._focused:
            phase = (self._blink_t / max(0.05, self._blink_interval)) % 2.0
            if phase < 0.75:
                cursor_alpha = 1.0
            elif phase < 1.0:
                cursor_alpha = 1.0 - (phase - 0.75) / 0.25
            else:
                cursor_alpha = 0.0
        else:
            cursor_alpha = 0.0

        radius = (min(rect.height // 2, 14) if border_radius is None
                  else int(border_radius))
        scroll_i = int(round(self._scroll))
        sel_a, sel_b = self._sel_range()

        key = (
            rect.width, rect.height, radius,
            tuple(bg_color[:3]), tuple(bg_focus_color[:3]),
            tuple(border_color[:3]), tuple(border_focus_color[:3]),
            tuple(text_color[:3]), tuple(placeholder_color[:3]),
            tuple(selection_color[:4]), tuple(cursor_color[:3]),
            self._step(self._focus_progress),
            self._step(self._hover_progress),
            display, self._cursor, sel_a, sel_b,
            scroll_i, self._step(cursor_alpha),
            self._font_size, self._font_path,
        )
        if key != self._surf_key:
            self._surf = self._compose(
                rect.width, rect.height, radius, font, display,
                cursor_px, cursor_alpha, scroll_i,
                (bg_color, bg_focus_color, border_color, border_focus_color,
                 text_color, placeholder_color, selection_color, cursor_color))
            self._surf_key = key

        surface.blit(self._surf, (rect.x - GLOW, rect.y - GLOW))

    # ------------------------------------------------------------------ #
    def _compose(self, width, height, radius, font, display,
                 cursor_px, cursor_alpha, scroll, colors):
        """合成整个输入框到一张 (宽 + 2*GLOW, 高 + 2*GLOW) 的表面。"""
        (bg_color, bg_focus_color, border_color, border_focus_color,
         text_color, placeholder_color, selection_color, cursor_color) = colors

        focus = self._focus_progress
        hover = self._hover_progress

        total_w = width + 2 * GLOW
        total_h = height + 2 * GLOW

        # ---------- 超采样画布 ----------
        big = pygame.Surface((total_w * AA, total_h * AA), pygame.SRCALPHA)
        inner = pygame.Rect(GLOW * AA, GLOW * AA, width * AA, height * AA)
        rr = max(1, int(radius * AA))

        # ---------- 聚焦外发光（多层描边模拟柔和光晕） ----------
        if focus > 0.01:
            glow_rgb = border_focus_color[:3]
            for k in range(GLOW, 0, -1):
                t = k / GLOW                      # 1 = 最外圈
                a = int(focus * 90 * (1.0 - t) ** 2)
                if a <= 0:
                    continue
                grow = k * AA
                ring = inner.inflate(grow * 2, grow * 2)
                pygame.draw.rect(big, (*glow_rgb, a), ring,
                                 width=AA * 2, border_radius=rr + grow)

        # ---------- 底色 ----------
        bg = _lerp_color(bg_color, bg_focus_color, focus)
        if hover > 0:
            bg = tuple(min(255, int(c + (255 - c) * hover * 0.05)) for c in bg)
        pygame.draw.rect(big, (*bg, 255), inner, border_radius=rr)

        # ---------- 边框（聚焦时加粗 + 变亮） ----------
        bcol = _lerp_color(border_color, border_focus_color, focus)
        bw = max(1, int(round((1.0 + 0.6 * focus) * AA)))
        pygame.draw.rect(big, (*bcol, 255), inner, width=bw, border_radius=rr)

        # ---------- 降采样 ----------
        surf = pygame.transform.smoothscale(big, (total_w, total_h))

        # ---------- 文本区 ----------
        pad = self._pad_x(height)
        area = pygame.Rect(GLOW + pad, GLOW, max(1, width - 2 * pad), height)

        fh = font.get_height()
        ty = GLOW + (height - fh) // 2

        old_clip = surf.get_clip()
        surf.set_clip(area)

        text_col = tuple(text_color[:3])
        sel_col = (tuple(selection_color[:4]) if len(selection_color) >= 4
                   else (*selection_color[:3], 110))

        sel_a, sel_b = self._sel_range()

        # 占位符
        if not self._text and self._placeholder:
            ph = font.render(self._placeholder, True,
                             tuple(placeholder_color[:3]))
            surf.blit(ph, (area.x, ty))

        # 选区高亮
        if sel_a != sel_b:
            x0 = area.x + font.size(display[:sel_a])[0] - scroll
            x1 = area.x + font.size(display[:sel_b])[0] - scroll
            if x1 > x0:
                sel_rect = pygame.Rect(int(x0), ty + 1,
                                       max(1, int(x1 - x0)), max(2, fh - 2))
                pygame.draw.rect(surf, sel_col, sel_rect, border_radius=3)

        # 正文
        if self._text:
            ts = font.render(display, True, text_col)
            surf.blit(ts, (area.x - scroll, ty))

        # 光标
        if cursor_alpha > 0.01:
            cx = area.x + cursor_px - scroll
            cw = max(2, int(round(height * 0.06)))
            crest = pygame.Rect(int(cx), ty + 2, cw, max(2, fh - 4))
            pygame.draw.rect(surf, (*cursor_color[:3], int(255 * cursor_alpha)),
                             crest, border_radius=cw // 2)

        surf.set_clip(old_clip)
        return surf