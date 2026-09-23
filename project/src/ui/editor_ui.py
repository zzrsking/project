# src/ui/editor_ui.py
"""编辑器界面：左侧「工具 / 图层」侧栏、顶部模式按钮、底部状态栏。

视觉规范统一在 src/ui/elements/theme.py —— 与 button / switch / inputbox /
scrollbar 共用同一套配色、圆角与强调色，不再各写各的魔法数字。

性能：
  · 静态外壳（侧栏底、卡片、投影、小标题）按 (尺寸, 工具数, 图层数) 预渲染成
    表面缓存起来，静止帧只做一次 blit，不必每帧重画几十个矩形；
  · 外壳只覆盖侧栏与状态栏两个小区域，不做整屏 alpha blit；
  · 字体一律走 fonts.get_font 缓存（旧实现每帧 new 一个 Font）。
"""
import pygame

from src.ui.elements.button import UIButton, ButtonGroup
from src.ui.elements.fonts import get_font
from src.ui.elements import theme
from src.editor.property_editor import PropertyEditor
from utils.helpers import print_info


# ---------------- 布局常量：绘制与命中测试共用同一份，避免两边漂移 ----------------
SIDEBAR_W = 350
STATUS_H = 30

CARD_TOP = 10
CARD_HEADER_H = 28
CARD_PAD = 10

TOOLS_CARD_X = 10
TOOLS_CARD_W = 180
LAYERS_CARD_X = 200
LAYERS_CARD_W = 140

TOOL_BTN_H = 42
TOOL_BTN_GAP = 8
LAYER_BTN_H = 30
LAYER_BTN_GAP = 6

TITLE_FONT_SIZE = 14
TOOL_FONT_SIZE = 17
LAYER_FONT_SIZE = 15
MODE_FONT_SIZE = 17
STATUS_FONT_SIZE = 16

# 组内所有按钮共用同一个强调色。
# 以前按工具类型逐个配色（绿/蓝/橙/紫/粉/红…），整个侧栏会变成调色盘 ——
# 现在统一成"中性面 + 唯一强调色"：未选中的全是素净的中性面，只有选中那颗亮起来。
GROUP_ACCENT = theme.ACCENT


def _tools_card_h(n):
    if n <= 0:
        return CARD_HEADER_H + CARD_PAD
    return (CARD_HEADER_H + CARD_PAD + n * TOOL_BTN_H
            + (n - 1) * TOOL_BTN_GAP + CARD_PAD)


def _layers_card_h(n):
    if n <= 0:
        return CARD_HEADER_H + CARD_PAD
    return (CARD_HEADER_H + CARD_PAD + n * LAYER_BTN_H
            + (n - 1) * LAYER_BTN_GAP + CARD_PAD)


def _tool_rect(i):
    return pygame.Rect(
        TOOLS_CARD_X + CARD_PAD,
        CARD_TOP + CARD_HEADER_H + CARD_PAD + i * (TOOL_BTN_H + TOOL_BTN_GAP),
        TOOLS_CARD_W - 2 * CARD_PAD,
        TOOL_BTN_H)


def _layer_rect(i):
    return pygame.Rect(
        LAYERS_CARD_X + CARD_PAD,
        CARD_TOP + CARD_HEADER_H + CARD_PAD + i * (LAYER_BTN_H + LAYER_BTN_GAP),
        LAYERS_CARD_W - 2 * CARD_PAD,
        LAYER_BTN_H)


class EditorInterface:
    def __init__(self, screen_rect, world, editor):
        self.screen_rect = screen_rect
        self.world = world
        self.editor = editor

        # ----- 侧栏 -----
        self.sidebar_width = SIDEBAR_W
        self.sidebar_rect = pygame.Rect(0, 0, SIDEBAR_W,
                                        screen_rect.height - STATUS_H)

        # 卡片矩形（绘制 + 按钮布局共用）
        n_tools = len(getattr(editor, "editor_tools", ()) or ())
        n_layers = len(list(world.layers.names()))
        self.tools_panel = pygame.Rect(
            TOOLS_CARD_X, CARD_TOP, TOOLS_CARD_W, _tools_card_h(n_tools))
        self.layer_panel = pygame.Rect(
            LAYERS_CARD_X, CARD_TOP, LAYERS_CARD_W, _layers_card_h(n_layers))

        # ----- 状态栏 -----
        self.status_bar = pygame.Rect(0, screen_rect.height - STATUS_H,
                                      screen_rect.width, STATUS_H)

        # ----- 属性面板 -----
        prop_rect = pygame.Rect(0, 0, 350, 800)
        self.property_panel = PropertyEditor(None, prop_rect, editor)

        # ----- 按钮 -----
        # mode_buttons：模式切换是“独立(非分组)动作按钮”，不放进 ButtonGroup。
        # 独立按钮(group=None)默认处于选中态(彩色)，同时仍响应悬停高光。
        self.mode_buttons = []
        self.tool_buttons = ButtonGroup()
        self.layer_buttons = ButtonGroup()
        self._create_mode_buttons()
        self._create_tool_buttons()
        self._create_layer_buttons()
        self._init_default_selection()

        self.selected_tool_index = 0
        self.selected_layer_index = 0

        # 静态外壳缓存
        self._chrome_side = None
        self._chrome_side_key = None
        self._chrome_status = None

        # 订阅层变更：图层按钮始终从单一真值源(LayerStack)重建，不再快照漂移
        self.world.layers.subscribe(self._on_layers_changed)

    # ------------------------------------------------------------------
    # 按钮创建
    # ------------------------------------------------------------------
    def _create_mode_buttons(self):
        width = self.world.screen_rect.width
        # 主按钮(Play)：强调色实心；次级按钮(Settings)：中性面 + 描边，只在悬停时出强调色。
        # 两个都是独立按钮(group=None)，不参与"组内选中"那套。
        self.mode_buttons = [
            UIButton(pygame.Rect(width - 310, 12, 100, 36), "Play", theme.PRIMARY,
                     opaque=True),
            UIButton(pygame.Rect(width - 200, 12, 100, 36), "Settings", theme.ACCENT,
                     opaque=True, filled=False),
        ]

    def _create_tool_buttons(self):
        tools = getattr(self.editor, "editor_tools", ()) or ()
        for i, tool in enumerate(tools):
            self.tool_buttons.append(
                UIButton(_tool_rect(i), tool["name"], GROUP_ACCENT, opaque=True))
        self._sync_tool_selection()

    def _create_layer_buttons(self):
        layers = list(self.world.layers.names())
        for i, layer in enumerate(layers):
            btn = UIButton(_layer_rect(i), layer.capitalize(), GROUP_ACCENT,
                           opaque=True)
            # 身份存原始图层名：按钮上的文字是 capitalize 过的显示名，
            # 拿它去和 editor.selected_layer("terrain") 比会永远比不中。
            btn.layer_name = layer
            self.layer_buttons.append(btn)
        self._sync_layer_selection()

    def _sync_tool_selection(self):
        """按钮组高亮**严格跟随** editor.selected_tool。

        旧实现只在 "有选中" 时才同步，且拿显示文字去比对，于是：
          · 启动时 editor.selected_tool 还是 None，按钮组却已自动选中第一个
            -> "默认高亮框"和"实际选中的工具"不一致；
          · 图层名是 "terrain"、按钮文字是 "Terrain"，永远比不中
            -> 图层高亮永远停在被自动选中的第一个上（就是那个 bug）。
        现在反过来：找不到匹配就把高亮清掉，绝不留陈旧状态。
        """
        tool = getattr(self.editor, "selected_tool", None)
        want = tool.get("name") if tool else None
        self._select_in_group(self.tool_buttons, want, "text")

    def _sync_layer_selection(self):
        self._select_in_group(self.layer_buttons,
                              getattr(self.editor, "selected_layer", None),
                              "layer_name")

    @staticmethod
    def _select_in_group(group, want, attr):
        """把 group 的高亮切到 attr 等于 want 的按钮；没有匹配就清空高亮。"""
        if want is not None:
            for btn in group:
                if getattr(btn, attr, None) == want:
                    group.select(btn)
                    return
        group.clear_selection()

    def _init_default_selection(self):
        """让"真值源"和按钮组的默认高亮对齐。

        ButtonGroup 加入第一个按钮时会自动把它选中；如果 editor 那边还是 None，
        面板上就会显示一个"其实并没被选中"的高亮框。这里把 editor 的默认
        工具/图层补上，两边从此一致。
        """
        if (getattr(self.editor, "selected_tool", None) is None
                and len(self.tool_buttons) and hasattr(self.editor, "select_tool")):
            self.editor.select_tool(0)

        if (getattr(self.editor, "selected_layer", None) is None
                and len(self.layer_buttons) and hasattr(self.editor, "select_layer")):
            names = list(self.world.layers.names())
            if names:
                self.editor.select_layer(names[0])

        self._sync_tool_selection()
        self._sync_layer_selection()

    # ------------------------------------------------------------------
    # 图层变更
    # ------------------------------------------------------------------
    def _on_layers_changed(self, kind, name):
        # 仅层定义变化(add/remove/reorder/visible/update)时重建；内容级(contents)不重建
        if kind == "contents":
            return
        # 先处理"当前图层已经不存在"，再重建按钮。
        # 反过来的话：重建时第一个按钮会被自动选中，随后才把 selected_layer 清成 None，
        # 于是高亮的和被选中的又对不上了。
        if self.editor.selected_layer not in self.world.layers:
            names = list(self.world.layers.names())
            self.editor.select_layer(names[0] if names else None)
        self.layer_buttons.clear()
        self._create_layer_buttons()
        self.invalidate_chrome()

    def update_buttons(self):
        self.layer_buttons.clear()
        self._create_layer_buttons()
        self.tool_buttons.clear()
        self._create_tool_buttons()
        self.mode_buttons.clear()
        self._create_mode_buttons()
        self.invalidate_chrome()

    def invalidate_chrome(self):
        self._chrome_side = None
        self._chrome_side_key = None

    # ------------------------------------------------------------------
    # 更新 / 事件
    # ------------------------------------------------------------------
    def update(self, mouse_pos):
        # 注意：绝不能写成 self.mode_buttons + self.tool_buttons + self.layer_buttons。
        # ButtonGroup 的 `+`/`+=` 运算符会通过 add_button() 把所有按钮重新加入一个临时组，
        # 这样每帧都会把按钮的 selected 清掉、_progress 归零、并把 .group 重指向临时组，
        # 导致点击后高光/颜色渐变/缓动动画全部失效（也波及独立按钮的始终选中态）。
        # 正确做法：分别遍历各个容器。
        for btn in self.mode_buttons:
            btn.check_hover(mouse_pos)
        for btn in self.tool_buttons:
            btn.check_hover(mouse_pos)
        for btn in self.layer_buttons:
            btn.check_hover(mouse_pos)

        # 每帧把高亮拉回真值源：外部（快捷键 / 工具逻辑 / 图层重建）改了选中项，
        # 面板也必须跟着变，不能各说各话
        self._sync_tool_selection()
        self._sync_layer_selection()

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # 用事件自带的点击坐标：和 pygame.mouse.get_pos() 在真实点击时一致，
            # 但事件坐标不会被"这一帧鼠标又移动了"影响，可测试性也更好。
            mouse_pos = event.pos
            for i, btn in enumerate(self.tool_buttons):
                if btn.rect.collidepoint(mouse_pos):
                    self.selected_tool_index = i
                    self.editor.select_tool(i)
                    self.tool_buttons.select(btn)
                    return True
            for i, btn in enumerate(self.layer_buttons):
                if btn.rect.collidepoint(mouse_pos):
                    self.selected_layer_index = i
                    layers = list(self.world.layers.names())
                    if 0 <= i < len(layers):
                        self.editor.select_layer(layers[i])
                        self.layer_buttons.select(btn)
                    return True
        return False

    # ------------------------------------------------------------------
    # 静态外壳（预渲染 + 缓存）
    # ------------------------------------------------------------------
    def _build_sidebar_chrome(self, n_tools, n_layers):
        """侧栏底 + 两张卡片 + 小标题，预渲染成一张仅覆盖侧栏的表面。"""
        sb = self.sidebar_rect
        surf = pygame.Surface((sb.width, sb.height), pygame.SRCALPHA)
        title_font = get_font(TITLE_FONT_SIZE)

        # 侧栏：半透明深色面 + 右缘分隔线（只圆右侧两角）
        pygame.draw.rect(surf, (*theme.BG_SURFACE, 236),
                         (0, 0, sb.width, sb.height),
                         border_top_right_radius=theme.RADIUS_CARD,
                         border_bottom_right_radius=theme.RADIUS_CARD)
        pygame.draw.line(surf, theme.BORDER,
                         (sb.width - 1, 10), (sb.width - 1, sb.height - 10), 1)

        # 工具卡片
        self.tools_panel.height = _tools_card_h(n_tools)
        theme.draw_card(surf, self.tools_panel, shadow=12, shadow_alpha=110,
                        tint_alpha=246)
        theme.section_title(surf, "工具",
                            (self.tools_panel.x + CARD_PAD, self.tools_panel.y + 8),
                            title_font, accent=theme.ACCENT_SOFT)
        self._draw_count(surf, n_tools, self.tools_panel, title_font)

        # 图层卡片
        self.layer_panel.height = _layers_card_h(n_layers)
        theme.draw_card(surf, self.layer_panel, shadow=12, shadow_alpha=110,
                        tint_alpha=246)
        theme.section_title(surf, "图层",
                            (self.layer_panel.x + CARD_PAD, self.layer_panel.y + 8),
                            title_font, accent=theme.ACCENT)

        self._draw_count(surf, n_layers, self.layer_panel, title_font)
        return surf

    @staticmethod
    def _draw_count(surf, count, card, font):
        text = font.render(str(count), True, theme.TEXT_MUTED)
        surf.blit(text, (card.right - CARD_PAD - text.get_width(), card.y + 8))

    def _build_status_chrome(self):
        """状态栏底：不透明表面，blit 更快。"""
        bar = self.status_bar
        surf = pygame.Surface((bar.width, bar.height))
        surf.fill(theme.BG_DEEP)
        pygame.draw.line(surf, theme.BORDER, (0, 0), (bar.width, 0), 1)
        return surf

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def draw(self, surface):
        mouse_pos = pygame.mouse.get_pos()
        self.update(mouse_pos)

        n_tools = len(self.tool_buttons)
        n_layers = len(self.layer_buttons)

        key = (self.screen_rect.width, self.screen_rect.height, n_tools, n_layers)
        if key != self._chrome_side_key:
            self._chrome_side = self._build_sidebar_chrome(n_tools, n_layers)
            self._chrome_side_key = key
            self._chrome_status = self._build_status_chrome()

        # 侧栏外壳 + 工具/图层按钮
        surface.blit(self._chrome_side, (0, 0))
        for btn in self.tool_buttons:
            btn.draw(surface, TOOL_FONT_SIZE)
        for btn in self.layer_buttons:
            btn.draw(surface, LAYER_FONT_SIZE)

        # 顶部模式按钮
        for btn in self.mode_buttons:
            btn.draw(surface, MODE_FONT_SIZE)

        # 状态栏（底 + 坐标）
        surface.blit(self._chrome_status, self.status_bar.topleft)
        self.draw_coordinates(surface, mouse_pos)

        # 属性面板
        self.property_panel.draw(surface, mouse_pos)

        # 结构面板最后画：它是"呼出式浮层"，必须压在所有 UI 之上
        # （放在 editor_manager.draw_preview 里会被侧栏和属性面板盖住）
        palette = getattr(self.editor, "structure_palette", None)
        if palette is not None:
            palette.draw(surface, mouse_pos)

    def draw_coordinates(self, surface, mouse_pos):
        font = get_font(STATUS_FONT_SIZE)
        world_x, world_y = self.world.camera.screen_to_world(mouse_pos)

        x = self.status_bar.left + 12
        y = self.status_bar.top + (STATUS_H - font.get_height()) // 2

        surface.blit(font.render("屏幕", True, theme.TEXT_MUTED), (x, y))
        x += font.size("屏幕")[0] + 8
        screen_coord = f"{mouse_pos[0]}, {mouse_pos[1]}"
        surface.blit(font.render(screen_coord, True, theme.TEXT), (x, y))
        x += font.size(screen_coord)[0] + 16

        pygame.draw.line(surface, theme.BORDER,
                         (x, self.status_bar.top + 8),
                         (x, self.status_bar.bottom - 8), 1)
        x += 16

        surface.blit(font.render("世界", True, theme.TEXT_MUTED), (x, y))
        x += font.size("世界")[0] + 8
        surface.blit(
            font.render(f"{world_x:.1f}, {world_y:.1f}", True, theme.TEXT), (x, y))
