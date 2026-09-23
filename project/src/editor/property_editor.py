import pygame
from collections import OrderedDict
from pygame import MOUSEBUTTONDOWN, KEYDOWN, K_RETURN, K_BACKSPACE

from src.ui.elements.button import UIButton
from src.ui.elements.fonts import get_font
from src.ui.elements import theme
from src.ui.elements.ime import ImeCursor, start as ime_start, stop as ime_stop
# 剪贴板直接复用 InputBox 的实现（tkinter 可选依赖，拿不到就静默降级）
from src.ui.elements.inputbox import get_clipboard_text, set_clipboard_text
from src.utils.helpers import print_info
from src.editor.tools import is_editor_object


# ======================================================================
# 面板布局常量：draw() 与 handle_input() 共用同一套几何，避免"画的框"和
# "点的框"各写一份坐标后慢慢漂移（旧代码就是两处各写了一遍 100/150/24/30）。
# ======================================================================
FONT_SIZE = 15
HEADER_FONT_SIZE = 17
PANEL_WIDTH = 320
HEADER_H = 46             # 顶部标题栏高度
ROW_HEIGHT = 32
ROW_TOP = HEADER_H + 6
FIELD_LABEL_X = 14
INPUT_X = 118
INPUT_WIDTH = PANEL_WIDTH - INPUT_X - 14
INPUT_HEIGHT = 26
INPUT_TEXT_PAD = 8        # 输入框内文字的左内边距（_notify_ime 也用它）
BUTTON_AREA = 48          # 底部给 OK/Cancel 留的高度
ALPHA_STEPS = 16          # 淡入淡出的透明度量化档数（缓存用）
PANEL_SHADOW = 12         # 面板投影向外扩散的像素
CARET_BLINK = 0.53        # 输入光标闪烁半周期（秒），与 InputBox 保持一致
CARET_STEPS = 8           # 光标透明度量化档数（缓存用）
# 按键重复：自实现，SDL2 默认不开 set_repeat（与 InputBox 同参数）
REPEAT_DELAY = 0.40       # 按住多久后开始重复
REPEAT_INTERVAL = 0.04    # 之后每隔多久重复一次
_REPEATABLE_KEYS = frozenset({
    pygame.K_LEFT, pygame.K_RIGHT, pygame.K_HOME, pygame.K_END,
    pygame.K_BACKSPACE, pygame.K_DELETE,
})

_TRUE_WORDS = {"true", "1", "yes", "y", "on", "是", "真"}
_FALSE_WORDS = {"false", "0", "no", "n", "off", "否", "假"}


class PropertyEditor:
    """属性编辑器，处理对象属性修改。

    性能说明（本次优化重点）：
      · 字段快照只在"对象或其状态真的变了"时重建（旧实现被 EditorManager 每帧调用，
        每帧重建一个 12 项 OrderedDict + 12 次 str()）；
      · 面板表面按 (字段内容, 选中字段, 透明度档, 是否编辑, 尺寸) 缓存，静止帧只 blit
        （旧实现每帧 new 一个 300x410 的 SRCALPHA 表面并渲染 24 段文字）；
      · 字体走全局缓存，文字按 (文本, 颜色, 透明度) 缓存。
    """

    def __init__(self, obj, exclusion_zone: pygame.rect.Rect, editor):
        # editor 必须先赋值：update_obj 里要用 editor.world 反查图层
        self.editor = editor

        self.fields = OrderedDict()
        self.obj = None
        self._fields_key = None

        self.rect = pygame.rect.Rect(0, 0, PANEL_WIDTH, 0)
        self.active_field_index = None
        self.can_update_position = True
        self.exclusion_zone = exclusion_zone
        self.screen_rect = self.editor.world.screen_rect

        # 透明度
        self.alpha = 0                      # 0-255
        self.fade_speed = 11 * 10 ** 3 / 6  # 淡入淡出速度
        self.show = False
        self.is_editing = False
        self.locked_obj = None              # 当前锁定编辑的对象

        self.edit_bg_color = (*theme.CARD_BG_HI, 255)     # 编辑状态底色
        self.normal_bg_color = (*theme.CARD_BG, 255)      # 普通状态底色

        # 缓存
        self._panel_surf = None
        self._panel_key = None
        self._text_cache = {}

        # 投影缓存（尺寸变化才重建）
        self._shadow_surf = None
        self._shadow_key = None

        # 输入光标闪烁计时
        self._caret_t = 0.0
        self._last_ticks = pygame.time.get_ticks()

        # 文本编辑状态（对标 InputBox）：光标 / 选区锚点 / 拖拽选择 / 长按重复
        self._cursor = 0
        self._anchor = 0
        self._dragging = False
        self._held = {}

        # [IME] 输入法候选框跟随当前编辑字段的光标
        self._ime = ImeCursor()

        self.button_ok = UIButton(pygame.Rect(0, 0, 64, 28), "OK", theme.OK_GREEN)
        self.button_cancel = UIButton(pygame.Rect(0, 0, 64, 28), "Cancel",
                                      theme.CANCEL_RED)

        self.update_obj(obj)
        self.update_size()

    # ------------------------------------------------------------------
    # 布局（唯一真值源）
    # ------------------------------------------------------------------
    def _row_y(self, index):
        return ROW_TOP + index * ROW_HEIGHT

    def _input_rect(self, index):
        """第 index 个字段的输入框矩形（面板局部坐标）。"""
        return pygame.Rect(INPUT_X, self._row_y(index) + 2, INPUT_WIDTH, INPUT_HEIGHT)

    def update_size(self):
        """根据字段数量自动调整面板尺寸"""
        rows = len(self.fields)
        self.rect.height = ROW_TOP + rows * ROW_HEIGHT + BUTTON_AREA
        self.rect.width = PANEL_WIDTH

        # 按钮位置调整到右下角（面板局部坐标，draw 时画进面板表面）
        self.button_ok.rect.bottomright = (self.rect.w - 14, self.rect.h - 14)
        self.button_cancel.rect.bottomright = (self.rect.w - 14 - 74, self.rect.h - 14)

    def invalidate(self):
        """丢弃面板缓存（字段之外的显示状态变化时用）。"""
        self._panel_surf = None
        self._panel_key = None

    # ------------------------------------------------------------------
    # 淡入淡出
    # ------------------------------------------------------------------
    def start_fade_in(self, delta_time):
        if not self.is_editing:
            self.show = True
            self.alpha = min(self.alpha + self.fade_speed * delta_time, 255)

    def start_fade_out(self, delta_time):
        if not self.is_editing:
            self.alpha = max(self.alpha - self.fade_speed * delta_time, 0)
            if self.alpha == 0:
                self.show = False
                self.locked_obj = None

    def update_position(self, new_position):
        if self.can_update_position:
            temp_rect = self.rect.copy()
            temp_rect.topleft = new_position
            temp_rect.clamp_ip(self.screen_rect)
            self.rect.topleft = temp_rect.topleft

    # ------------------------------------------------------------------
    # 字段快照
    # ------------------------------------------------------------------
    def _layer_name_of(self, obj):
        bucket = self.editor.world.layers.find_bucket_of(obj)
        return bucket.spec.name if bucket is not None else "None"

    def _object_state_key(self, obj):
        """决定"面板内容是否需要重读"的廉价指纹。

        读 10 来个属性即可，比整份 OrderedDict 重建 + 12 次 str() 便宜得多；
        同时保留"对象动了，面板里的 x/y 跟着刷新"的旧行为。
        """
        wp = getattr(obj, "world_position", None)
        props = getattr(obj, "properties", None)
        return (id(obj), self._layer_name_of(obj),
                getattr(wp, "x", None), getattr(wp, "y", None),
                getattr(wp, "width", None), getattr(wp, "height", None),
                getattr(obj, "interact_type", None),
                (props or {}).get("name") if isinstance(props, dict) else None,
                bool(getattr(obj, "is_collidable", False)),
                bool(getattr(obj, "bind_camera", None)),
                getattr(obj, "img_angle", None))

    def _read_fields(self, obj):
        """读取一份字段快照。

        逐项按"对象有没有这个属性"决定是否收录：旧实现把 12 项塞进一个 try 里，
        只要对象是地形瓦片(Tiled，没有 interact_type/properties) 就整块失败 ——
        面板变成空白，而且每帧往控制台打一条异常。
        """
        values = OrderedDict()
        props = getattr(obj, "properties", None)
        values["interact_type"] = str(getattr(obj, "interact_type", "none"))
        values["name"] = str(props.get("name", "")) if isinstance(props, dict) else ""
        values["layer"] = self._layer_name_of(obj)

        wp = getattr(obj, "world_position", None)
        if wp is not None:
            values["x"] = str(wp.x)
            values["y"] = str(wp.y)
            values["centerx"] = str(wp.centerx)
            values["centery"] = str(wp.centery)

        if hasattr(obj, "is_collidable"):
            values["collision"] = str(bool(obj.is_collidable))
        if hasattr(obj, "bind_camera"):
            values["set_bind"] = str(bool(obj.bind_camera))
        if hasattr(obj, "img_angle"):
            values["angle"] = str(obj.img_angle)
        # 一次性动作开关：由用户在面板里输入 true 触发（catch=抓住本对象 / receive=接收抓住的对象）
        values["catch"] = "True" if self.editor.catch is obj else "False"
        values["receive"] = "False"
        return values

    def update_obj(self, new_obj):
        """切换/刷新面板显示的对象。同一对象且状态未变时直接返回（可安全每帧调用）。

        :param new_obj: 目标对象；地形瓦片(Tiled)等"不归编辑器管"的对象会被当成 None，
                        任何调用方都无法把瓦片塞进面板。
        """
        if new_obj is not None and not is_editor_object(new_obj):
            new_obj = None
        if new_obj is None:
            self.obj = None
            self._fields_key = None
            if self.fields:
                self.fields = OrderedDict()
                self.update_size()
                self.invalidate()
            return

        state_key = self._object_state_key(new_obj)
        if new_obj is self.obj and state_key == self._fields_key:
            return
        self.obj = new_obj
        self._fields_key = state_key
        self.fields = self._read_fields(new_obj)
        self.update_size()
        self.invalidate()

    def update_button_rect(self, font_size, longest_rect):
        # 自动化检测和更新编辑器的长宽
        ...

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def draw(self, surface, mouse_pos):
        # 光标闪烁计时：即使这一帧面板不画（鼠标压在侧栏/淡出中）也要继续走，
        # 否则重新出现时相位从 0 重来，会"跳"一下。
        now = pygame.time.get_ticks()
        dt = (now - self._last_ticks) / 1000.0
        self._last_ticks = now
        if self.is_editing and self.active_field_index is not None:
            self._caret_t += dt
            self._step_key_repeat(dt)
        else:
            self._caret_t = 0.0
            self._held.clear()

        if not self.show or self.exclusion_zone.collidepoint(mouse_pos):
            return

        # [IME] 面板可能被拖动过，每帧把候选框位置对齐到当前字段
        self._notify_ime()

        # 按钮位置调整到右下角（与 update_size 保持一致）
        self.button_ok.rect.bottomright = (self.rect.w - 14, self.rect.h - 14)
        self.button_cancel.rect.bottomright = (self.rect.w - 14 - 74, self.rect.h - 14)

        alpha_step = int(self.alpha / 255 * ALPHA_STEPS + 0.5)
        # 光标的闪烁相位必须进缓存键，否则面板表面会一直复用、光标永远不动；
        # 光标位置/选区同理 —— 它们决定选高亮和光标的落点。
        caret_step = int(self._caret_alpha() * CARET_STEPS + 0.5)
        key = (self._fields_signature(), self.active_field_index, alpha_step,
               caret_step, self.is_editing, self.rect.size,
               self._cursor, self._anchor)
        if key != self._panel_key:
            self._panel_surf = self._compose_panel(alpha_step, caret_step / CARET_STEPS)
            self._panel_key = key

        # 投影：先画（压在面板下面），并随淡入淡出一同变淡
        if self._shadow_key != self.rect.size:
            self._shadow_surf = self._make_shadow(self.rect.size)
            self._shadow_key = self.rect.size
        self._shadow_surf.set_alpha(self.alpha)
        surface.blit(self._shadow_surf,
                     (self.rect.x - PANEL_SHADOW, self.rect.y - PANEL_SHADOW + 3))

        surface.blit(self._panel_surf, self.rect.topleft)

    def _fields_signature(self):
        return (tuple(self.fields.keys()), tuple(self.fields.values()))

    def _caret_alpha(self):
        """输入光标的当前不透明度（0~1）。末段淡出，比硬闪更柔和。

        形状与 InputBox 的 cursor_alpha 一致：相位 0~0.75 全亮，0.75~1 淡出，
        1~2 全灭。
        """
        if not self.is_editing or self.active_field_index is None:
            return 0.0
        phase = (self._caret_t / max(0.05, CARET_BLINK)) % 2.0
        if phase < 0.75:
            return 1.0
        if phase < 1.0:
            return 1.0 - (phase - 0.75) / 0.25
        return 0.0

    def _compose_panel(self, alpha_step, caret_alpha=0.0):
        """合成整块面板（现代卡片：标题栏 + 圆角输入框 + 底部按钮）。"""
        # 用档位值反算 alpha：同一档内像素完全一致，缓存才真的命中
        alpha = int(round(alpha_step / ALPHA_STEPS * 255))
        bg_alpha = int(alpha * (0.88 if self.is_editing else 0.72))
        w, h = self.rect.size

        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        # ---- 卡片底 + 描边（编辑时描边转为强调色） ----
        bg = self.edit_bg_color if self.is_editing else self.normal_bg_color
        edge = theme.ACCENT_SOFT if self.is_editing else theme.BORDER
        pygame.draw.rect(panel, (*bg[:3], bg_alpha), (0, 0, w, h),
                         border_radius=theme.RADIUS_CARD)
        pygame.draw.rect(panel, (*edge, alpha), (0, 0, w, h), width=1,
                         border_radius=theme.RADIUS_CARD)

        font = get_font(FONT_SIZE)
        head_font = get_font(HEADER_FONT_SIZE)

        # ---- 标题栏 ----
        pygame.draw.rect(panel, (*theme.ACCENT_SOFT, alpha),
                         (FIELD_LABEL_X, 14, 3, 16), border_radius=2)
        layer = str(self.fields.get("layer", "") or "")
        chip_w = self._header_chip_width(head_font, layer) if layer else 0
        title_x = FIELD_LABEL_X + 11
        # 标题必须给右侧图层 chip 让位，否则长对象名会糊到 chip 上
        title_max = w - title_x - FIELD_LABEL_X - (chip_w + 10 if layer else 0)
        title = self._truncate(self._header_title(), head_font, title_max)
        panel.blit(self._render_cached(title, head_font, theme.TEXT, alpha),
                   (title_x, 12))
        if layer:
            self._draw_header_chip(panel, layer, head_font, alpha, chip_w)
        pygame.draw.line(panel, (*theme.BORDER, alpha),
                         (0, HEADER_H - 1), (w, HEADER_H - 1), 1)

        # ---- 字段行 ----
        for i, (field, value) in enumerate(self.fields.items()):
            active = (i == self.active_field_index)
            y = self._row_y(i)
            rect = self._input_rect(i)

            # 标签
            label_color = theme.ACCENT_HI if active else theme.TEXT_DIM
            label = self._render_cached(str(field), font, label_color, alpha)
            panel.blit(label, (FIELD_LABEL_X,
                               rect.centery - label.get_height() // 2))

            # 输入框
            pygame.draw.rect(panel, (*theme.CARD_BG_HI, bg_alpha), rect,
                             border_radius=theme.RADIUS_ROW)
            edge_col = (*theme.ACCENT_SOFT, alpha) if active else (*theme.BORDER, alpha)
            pygame.draw.rect(panel, edge_col, rect, width=1,
                             border_radius=theme.RADIUS_ROW)

            # 值（对标 InputBox：选区高亮 + 随光标水平滚动 + 闪烁光标）
            text = str(value)
            value_surf = self._render_cached(text, font, theme.TEXT, alpha)
            avail = rect.width - INPUT_TEXT_PAD * 2
            caret_index = self._cursor if active else len(text)
            shift = self._text_shift(font, text, caret_index)
            tx = rect.x + INPUT_TEXT_PAD - shift

            old_clip = panel.get_clip()
            panel.set_clip(rect.inflate(-INPUT_TEXT_PAD * 2, -4))

            # 选区高亮（画在文字下面）
            sel_a, sel_b = self._sel_range() if active else (0, 0)
            if active and sel_a != sel_b:
                x0 = tx + font.size(text[:sel_a])[0]
                x1 = tx + font.size(text[:sel_b])[0]
                if x1 > x0:
                    fh = font.get_height()
                    pygame.draw.rect(
                        panel, (*theme.ACCENT_SOFT, min(alpha, 120)),
                        pygame.Rect(int(x0), rect.centery - fh // 2,
                                    max(1, int(x1 - x0)), fh),
                        border_radius=2)

            panel.blit(value_surf,
                       (tx, rect.centery - value_surf.get_height() // 2))

            # 输入光标（闪烁）
            if active and caret_alpha > 0.01:
                caret_h = min(rect.height - 10, font.get_height())
                caret_x = tx + font.size(text[:caret_index])[0]
                caret = pygame.Rect(int(caret_x),
                                    rect.centery - caret_h // 2, 2, caret_h)
                pygame.draw.rect(panel, (*theme.ACCENT_HI, int(255 * caret_alpha)),
                                 caret, border_radius=1)
            panel.set_clip(old_clip)

        # 操作按钮（仅在编辑模式显示）
        if self.is_editing:
            self.button_ok.draw(panel, 15)
            self.button_cancel.draw(panel, 15)

        return panel

    # ------------------------------------------------------------------
    # 标题栏辅助
    # ------------------------------------------------------------------
    def _header_title(self):
        name = str(self.fields.get("name", "") or "").strip()
        return name if name else "属性"

    @staticmethod
    def _truncate(text, font, max_w):
        """按像素宽度截断文本，超出部分用省略号（二分查找，别逐字符试）。"""
        if max_w <= 0:
            return ""
        if font.size(text)[0] <= max_w:
            return text
        ell = "…"
        ell_w = font.size(ell)[0]
        if ell_w > max_w:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size(text[:mid])[0] + ell_w <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell

    def _header_chip_width(self, font, layer):
        return font.size(layer)[0] + 16

    def _draw_header_chip(self, panel, layer, font, alpha, chip_w):
        w = self.rect.w
        rect = pygame.Rect(w - FIELD_LABEL_X - chip_w, 13, chip_w, 20)
        pygame.draw.rect(panel, (*theme.CARD_BG_HI, alpha), rect,
                         border_radius=theme.RADIUS_ROW)
        pygame.draw.rect(panel, (*theme.BORDER, alpha), rect, width=1,
                         border_radius=theme.RADIUS_ROW)
        surf = self._render_cached(layer, font, theme.TEXT_DIM, alpha)
        panel.blit(surf, (rect.x + 8, rect.centery - surf.get_height() // 2))

    def _make_shadow(self, size):
        """面板投影：预渲染一次，尺寸变化才重建。"""
        w, h = size
        pad = PANEL_SHADOW
        surf = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
        for i in range(pad, 0, -2):
            a = int(110 * (1.0 - i / pad) ** 2)
            if a <= 0:
                continue
            r = pygame.Rect(pad - i, pad - i, w + i * 2, h + i * 2)
            pygame.draw.rect(surf, (*theme.SHADOW, a), r,
                             border_radius=theme.RADIUS_CARD + i)
        return surf

    def _render_cached(self, text, font, color, alpha):
        """文字渲染缓存。渲染出来的表面是新建的，set_alpha 不会污染共享资源。

        键里必须带字体：只按 (文本, 颜色, alpha) 缓存的话，同一个字符串用不同字号
        渲染会互相顶掉（标题和正文就可能撞车）。
        """
        key = (text, id(font), color, alpha)
        surf = self._text_cache.get(key)
        if surf is None:
            surf = font.render(text, True, color)
            if alpha < 255:
                surf.set_alpha(alpha)
            if len(self._text_cache) > 512:
                self._text_cache.clear()
            self._text_cache[key] = surf
        return surf

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # [IME] 让输入法候选框跟随当前编辑字段
    # ------------------------------------------------------------------
    def _notify_ime(self):
        """把当前编辑字段的文本末尾位置告诉 SDL，候选框就贴在那里。

        字段里的光标恒定在文本末尾（输入统一 append、退格删末位），
        文本起点与 _compose_panel 保持一致：字段矩形 x + 5。
        位置没变时 ImeCursor 内部会跳过，不会每帧重复调 SDL。
        """
        if not self.is_editing or self.active_field_index is None:
            self._ime.invalidate()
            return
        keys = list(self.fields.keys())
        i = self.active_field_index
        if not (0 <= i < len(keys)):
            self._ime.invalidate()
            return
        local = self._input_rect(i)
        text = str(self.fields[keys[i]])
        font = get_font(FONT_SIZE)
        shift = self._text_shift(font, text, self._cursor)
        # 与 _compose_panel 里光标的落点保持一致：候选框贴住当前光标
        cx = self.rect.x + local.x + INPUT_TEXT_PAD - shift + font.size(text[:self._cursor])[0]
        cy = self.rect.y + local.y
        self._ime.follow(cx, cy, w=1, h=local.height)

    def _active_field_key(self):
        if self.active_field_index is None:
            return None
        keys = list(self.fields.keys())
        if 0 <= self.active_field_index < len(keys):
            return keys[self.active_field_index]
        return None

    def exit_edit_mode(self, delta_time):
        """退出编辑模式时重置状态"""
        self.is_editing = False
        self.active_field_index = None
        self.can_update_position = True
        # 清掉文本编辑状态（光标/选区/长按重复/拖拽）
        self._cursor = 0
        self._anchor = 0
        self._dragging = False
        self._held.clear()
        # [IME] 收起候选框
        self._ime.invalidate()
        ime_stop()
        self.start_fade_out(delta_time)
        self.update_size()  # 重置尺寸
        self.invalidate()

    def handle_input(self, event, cam, layers, delta_time, physics):
        if event.type == MOUSEBUTTONDOWN and self.is_editing:
            # 转换为面板相对坐标
            mouse_x = event.pos[0] - self.rect.left
            mouse_y = event.pos[1] - self.rect.top
            if self.button_ok.rect.collidepoint(mouse_x, mouse_y):
                self.apply_changes(cam, physics, layers)
                self.exit_edit_mode(delta_time)
                return True
            elif self.button_cancel.rect.collidepoint(mouse_x, mouse_y):
                self.exit_edit_mode(delta_time)
                return True

        if event.type == MOUSEBUTTONDOWN and event.button == 1:
            if not self.show:
                return False        # 面板没显示就不该有"哪个输入框被选中"
            mouse_x = event.pos[0] - self.rect.left
            mouse_y = event.pos[1] - self.rect.top
            self.active_field_index = None
            for i in range(len(self.fields)):
                if self._input_rect(i).collidepoint(mouse_x, mouse_y):
                    self.active_field_index = i
                    # 点哪就把光标放哪（对标 InputBox 的点击定位）
                    self._cursor = self._index_from_x(i, mouse_x)
                    self._anchor = self._cursor
                    self._dragging = True
                    self._caret_t = 0.0
                    self._held.clear()
                    break

            # [IME] 换字段/取消选中后位置要重算；没有字段在编辑就收起输入法
            self._ime.invalidate()
            if self.active_field_index is None:
                ime_stop()
            else:
                ime_start()
            return self.active_field_index is not None

        # ---- 拖拽选择 ----
        if event.type == pygame.MOUSEMOTION:
            if self._dragging and self.active_field_index is not None:
                if event.buttons[0]:
                    self._cursor = self._index_from_x(
                        self.active_field_index, event.pos[0] - self.rect.left)
                    self._caret_t = 0.0
                    return True
                self._dragging = False
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
            return False

        if event.type == pygame.KEYUP:
            self._held.pop(event.key, None)
            return False

        if self.active_field_index is None:
            return False

        if event.type == pygame.TEXTINPUT:
            self._insert(event.text)
            return True

        if event.type == KEYDOWN:
            if self._active_field_key() is None:
                return False
            if event.key == K_RETURN:
                # 回车 = 确认提交
                self.apply_changes(cam, physics, layers)
                self.exit_edit_mode(delta_time)
                return True
            # 可重复的键：交给 update 里的长按逻辑
            if event.key in _REPEATABLE_KEYS:
                if event.key in self._held:
                    return True          # 忽略 SDL 可能重复发来的 KEYDOWN
                self._held[event.key] = REPEAT_DELAY
            return self._apply_key(event.key, event.mod)
        return False

    # ------------------------------------------------------------------
    # 文本编辑（对标 InputBox：光标 / 选区 / 剪贴板 / 词级移动 / 长按重复）
    # ------------------------------------------------------------------
    def _step_key_repeat(self, dt):
        """长按连续重复：按住方向键/退格键时按固定节奏重复（与 InputBox 同逻辑）。"""
        if not self._held:
            return
        mods = pygame.key.get_mods()
        for k in list(self._held.keys()):
            self._held[k] -= dt
            if self._held[k] <= 0:
                self._apply_key(k, mods)
                self._held[k] = REPEAT_INTERVAL

    def _active_text(self):
        key = self._active_field_key()
        return self.fields.get(key, "") if key is not None else ""

    def _set_active_text(self, text):
        key = self._active_field_key()
        if key is not None:
            self.fields[key] = text
        self._caret_t = 0.0

    def _sel_range(self):
        if self._cursor < self._anchor:
            return self._cursor, self._anchor
        return self._anchor, self._cursor

    def _clamp_edit_state(self):
        n = len(self._active_text())
        self._cursor = max(0, min(self._cursor, n))
        self._anchor = max(0, min(self._anchor, n))

    def _replace_selection(self, s):
        """用 s 替换当前选区，并把光标放到插入内容之后。"""
        a, b = self._sel_range()
        text = self._active_text()
        self._set_active_text(text[:a] + s + text[b:])
        self._cursor = a + len(s)
        self._anchor = self._cursor

    def _word_move(self, direction):
        """Ctrl+左右：按词跳（与 InputBox 一致）。"""
        text = self._active_text()
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

    def _insert(self, s):
        if not s:
            return
        self._replace_selection(s)

    def _apply_key(self, key, mods):
        """处理一个编辑键；handle_input 与长按重复逻辑共用。"""
        if self._active_field_key() is None:
            return False
        shift = bool(mods & pygame.KMOD_SHIFT)
        ctrl = bool(mods & pygame.KMOD_CTRL)
        text = self._active_text()
        self._clamp_edit_state()

        if key == K_BACKSPACE:
            a, b = self._sel_range()
            if a != b:
                self._replace_selection("")
            elif self._cursor > 0:
                self._set_active_text(text[:self._cursor - 1] + text[self._cursor:])
                self._cursor -= 1
                self._anchor = self._cursor
            return True

        if key == pygame.K_DELETE:
            a, b = self._sel_range()
            if a != b:
                self._replace_selection("")
            elif self._cursor < len(text):
                self._set_active_text(text[:self._cursor] + text[self._cursor + 1:])
            return True

        if key in (pygame.K_LEFT, pygame.K_RIGHT):
            step = -1 if key == pygame.K_LEFT else 1
            new = self._word_move(step) if ctrl else self._cursor + step
            self._cursor = max(0, min(len(text), new))
            if not shift:
                self._anchor = self._cursor
            self._caret_t = 0.0
            return True

        if key == pygame.K_HOME:
            self._cursor = 0
            if not shift:
                self._anchor = 0
            self._caret_t = 0.0
            return True

        if key == pygame.K_END:
            self._cursor = len(text)
            if not shift:
                self._anchor = self._cursor
            self._caret_t = 0.0
            return True

        if ctrl and key == pygame.K_a:
            self._anchor = 0
            self._cursor = len(text)
            return True

        if ctrl and key == pygame.K_c:
            a, b = self._sel_range()
            if a != b:
                set_clipboard_text(text[a:b])
            return True

        if ctrl and key == pygame.K_x:
            a, b = self._sel_range()
            if a != b:
                set_clipboard_text(text[a:b])
                self._replace_selection("")
            return True

        if ctrl and key == pygame.K_v:
            self._insert(get_clipboard_text())
            return True

        return False

    def _index_from_x(self, field_index, panel_x):
        """把面板局部 x 换算成该字段文本里的字符索引（点击定位 / 拖拽选择用）。"""
        font = get_font(FONT_SIZE)
        text = str(self.fields.get(self._field_key_at(field_index), ""))
        rect = self._input_rect(field_index)
        local = panel_x - (rect.x + INPUT_TEXT_PAD) + self._text_shift(font, text)
        if local <= 0:
            return 0
        acc = 0
        for i, ch in enumerate(text):
            w = font.size(ch)[0]
            if local < acc + w * 0.5:
                return i
            acc += w
        return len(text)

    def _field_key_at(self, index):
        keys = list(self.fields.keys())
        if 0 <= index < len(keys):
            return keys[index]
        return None

    @staticmethod
    def _text_shift(font, text, cursor=None):
        """文本需要左移多少像素（保证光标可见；光标在末尾时就是"末尾可见"）。"""
        avail = INPUT_WIDTH - INPUT_TEXT_PAD * 2
        if cursor is None:
            cursor = len(text)
        caret_px = font.size(text[:cursor])[0]
        total_px = font.size(text)[0]
        max_shift = max(0, total_px + 4 - avail)
        return min(max(0, caret_px + 4 - avail), max_shift)

    # ------------------------------------------------------------------
    # 应用修改
    # ------------------------------------------------------------------
    @staticmethod
    def _as_bool(text, default=False):
        """宽松布尔解析："True"/"true"/"1"/"yes"/"是" 都算真。

        旧实现里 collision 用了 .lower()=="true"，而 set_bind/catch/receive 直接和
        小写字面量 "true" 比较 —— 面板默认显示的是 str(True)="True"，用户照着填反而无效。
        """
        if isinstance(text, bool):
            return text
        s = str(text).strip().lower()
        if s in _TRUE_WORDS:
            return True
        if s in _FALSE_WORDS:
            return False
        return default

    @staticmethod
    def _as_int(text, default=None):
        try:
            return int(str(text).strip())
        except (TypeError, ValueError):
            return default

    def apply_changes(self, cam, phy, layer):
        """把面板里的文本落到对象上。返回是否提交成功。"""
        obj = self.obj
        if obj is None:
            return False

        # ---- 文本字段 ----
        if "interact_type" in self.fields and hasattr(obj, "interact_type"):
            obj.interact_type = self.fields["interact_type"]
        props = getattr(obj, "properties", None)
        if "name" in self.fields and isinstance(props, dict):
            props["name"] = self.fields["name"]

        # ---- 位置：先按 x/y 判定，未变再看 centerx/centery ----
        moved = False
        wp = getattr(obj, "world_position", None)
        if wp is not None and "x" in self.fields:
            new_x = self._as_int(self.fields["x"], wp.x)
            new_y = self._as_int(self.fields["y"], wp.y)
            if (new_x, new_y) != (wp.x, wp.y):
                wp.x, wp.y = new_x, new_y
                moved = True
            else:
                # 旧实现在这里读的是 world_position.cety —— Rect 上没有这个属性，
                # 只要用户改的是 centerx/centery 就抛 AttributeError 崩掉。
                new_cx = self._as_int(self.fields.get("centerx"), wp.centerx)
                new_cy = self._as_int(self.fields.get("centery"), wp.centery)
                if (new_cx, new_cy) != (wp.centerx, wp.centery):
                    wp.centerx, wp.centery = new_cx, new_cy
                    moved = True
        if moved:
            self._sync_moved_object(obj, cam, phy)

        # ---- 角度 ----
        angle = self._as_int(self.fields.get("angle"), None)
        if angle is not None and hasattr(obj, "update_angle"):
            obj.update_angle(angle)

        # ---- 碰撞开关（只在真的变了时才动物理世界） ----
        if "collision" in self.fields and hasattr(obj, "is_collidable"):
            want = self._as_bool(self.fields["collision"], bool(obj.is_collidable))
            if want != bool(obj.is_collidable):
                obj.is_collidable = want
                if phy is not None:
                    if want:
                        phy.add_object(obj)
                    else:
                        phy.remove_object(obj)

        # ---- 相机绑定 / 一次性动作 ----
        if self._as_bool(self.fields.get("set_bind"), False) and cam is not None:
            cam.change_bind(obj)

        if self._as_bool(self.fields.get("catch"), False):
            self.editor.catch = obj
            print_info(f"[editor] catch = {obj}")

        if self._as_bool(self.fields.get("receive"), False):
            receiver = getattr(obj, "receive", None)
            if callable(receiver):
                receiver(self.editor.catch)
            else:
                print_info(f"[editor] {type(obj).__name__} 没有 receive()，已忽略")

        # 提交后强制刷新快照（catch 之类的字段值取决于编辑器状态，指纹里没有，
        # 所以先把指纹置空再 update_obj），免得面板继续显示旧值。
        self._fields_key = None
        self.update_obj(obj)
        return True

    def _sync_moved_object(self, obj, cam, phy):
        """位置改动后把各处状态推齐，否则这次改动会被抹掉/看不见。

        1. 物理体：不推位置的话，下一帧 sync_positions() 会把 world_position 拉回旧位置；
        2. 空间网格 + 渲染几何版本：不可碰撞对象不会被物理同步覆盖，网格会留在旧区块，
           静态地形缓存也不会重建（画面和数据脱节）。
        """
        if phy is not None and phy.get_body(obj) is not None:
            phy.teleport(obj, obj.world_position.center)

        world = getattr(self.editor, "world", None)
        if world is not None and hasattr(world, "notify_object_moved"):
            world.notify_object_moved(obj)

        if cam is not None and hasattr(obj, "update_rect"):
            obj.update_rect(cam.offset, getattr(cam, "zoom", 1.0))
