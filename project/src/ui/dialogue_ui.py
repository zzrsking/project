import pygame
from src.game.entity import Player
from src.dialogue.tree import DialogueOption
from src.dialogue.condition_registry import ConditionRegistry
from src.ui.elements.fonts import get_font

class DialogueInterface:
    def __init__(self, screen_rect=pygame.Rect(0,0,1200,800)):
        self.screen_rect = screen_rect
        # 面板参数
        self.panel = pygame.Rect(100, 100, 600, 450)
        self.option_buttons = []          # 存储 (rect, option_index)
        self.option_callback = None       # 由外部设置 (world.select_dialogue_option)
        self.current_node = None

        # ---- 新增：弹窗相关 ----
        self.popup_active = False         # 是否显示材料弹窗
        self.popup_rect = None            # 弹窗矩形
        self.pending_option_idx = -1
        self.requirement_details = {}     # 从注册表获取的详情
        self.confirm_callback = None      # 外部设置的确认回调 (world.confirm_dialogue_option)
        self.cancel_callback = None       # 外部设置的取消回调 (world.cancel_dialogue_option
        self.player = Player

        # 字体（全局缓存，避免每次进对话都重新解析字体文件）
        self.font = get_font(24)
        self.popup_font = get_font(20)

        # 文本换行 / 渲染缓存：对话面板每帧重画，正文和选项文字其实没变（同节点内）
        self._wrap_cache = {}
        self._text_cache = {}
        self._panel_cache = {}

        self._ending = False

    def update_ui(self, dialogue_node):
        """更新当前节点，重置状态"""
        self.current_node = dialogue_node
        self.option_buttons = []
        self._ending = False
        # 如果弹窗开着，关闭它（节点变了）
        if self.popup_active:
            self.popup_active = False
            self.pending_option_idx = -1

    def set_callbacks(self, option_cb, confirm_cb, cancel_cb):
        """设置回调函数：选项点击、确认提交、取消"""
        self.option_callback = option_cb
        self.confirm_callback = confirm_cb
        self.cancel_callback = cancel_cb

    # ---------- 事件处理 ----------
    def handle_event(self, event):
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False

        mouse_pos = pygame.mouse.get_pos()

        # 如果弹窗处于激活状态，处理弹窗上的按钮
        if self.popup_active and self.popup_rect:
            # 检查“提交”和“取消”按钮
            submit_rect = pygame.Rect(self.popup_rect.x + 20, self.popup_rect.bottom - 60, 100, 40)
            cancel_rect = pygame.Rect(self.popup_rect.right - 120, self.popup_rect.bottom - 60, 100, 40)
            if submit_rect.collidepoint(mouse_pos):
                if self.confirm_callback:
                    self.confirm_callback(self.pending_option_idx)
                # 注意：confirm_callback 会调用 world.confirm_dialogue_option，
                # 最终可能导致节点更新或保持原样，UI 会通过 update_ui 更新
                return True
            elif cancel_rect.collidepoint(mouse_pos):
                if self.cancel_callback:
                    self.cancel_callback()
                self.popup_active = False
                self.pending_option_idx = -1
                return True
            else:
                # 点击弹窗外部也可视为取消（可选）
                if not self.popup_rect.collidepoint(mouse_pos):
                    if self.cancel_callback:
                        self.cancel_callback()
                    self.popup_active = False
                    self.pending_option_idx = -1
                    return True
                return False

        # 非弹窗模式，处理选项按钮
        if not self.current_node or not self.option_callback:
            return False

        for rect, idx in self.option_buttons:
            if rect.collidepoint(mouse_pos):
                # 点击选项 -> 调用外部 option_callback（世界层将调用 manager.select_option）
                self.option_callback(idx)
                # 此时可能已经进入 PENDING 状态，但 UI 不知道，需要等待外部通知打开弹窗
                # 我们将在 draw 中检查 pending 状态，如果处于 PENDING，自动打开弹窗
                return True
        return False

    # ---------- 绘制 ----------
    def draw(self, surface, dialogue_node=None):
        node = dialogue_node if dialogue_node is not None else self.current_node
        if not node:
            # 如果节点为空但弹窗开着，也绘制弹窗（异常情况）
            if self.popup_active:
                self._draw_popup(surface)
            return

        # 绘制主面板
        self._draw_panel(surface, node)

        # 检查是否有待确认选项（由外部状态决定，我们通过对话管理器查询）
        # 由于我们无法直接访问 manager，需要通过外部回调或状态标记，我们可以依靠外部调用 open_popup
        # 但更稳健的做法：在 draw 时检查 world 的 dialogue_manager 状态。
        # 为解耦，由外部（world）在 select_option 后主动调用 dialogue_ui.open_popup
        # 所以我们只绘制弹窗，如果 popup_active 为 True
        if self.popup_active:
            self._draw_popup(surface)

    # ---------- 缓存辅助 ----------
    def _wrap_cached(self, text, max_width):
        key = (text, max_width)
        lines = self._wrap_cache.get(key)
        if lines is None:
            lines = self._wrap_text(text, max_width)
            if len(self._wrap_cache) > 32:
                self._wrap_cache.clear()
            self._wrap_cache[key] = lines
        return lines

    def _render_cached(self, text, font, color):
        key = (text, id(font), color)
        surf = self._text_cache.get(key)
        if surf is None:
            surf = font.render(text, True, color)
            if len(self._text_cache) > 64:
                self._text_cache.clear()
            self._text_cache[key] = surf
        return surf

    def _panel_surface(self, size, color):
        """按 (尺寸, 颜色) 缓存纯色半透明底板：对话面板每帧重画，底板本身是常量。"""
        key = (size, color)
        if key not in self._panel_cache:
            surf = pygame.Surface(size, pygame.SRCALPHA)
            surf.fill(color)
            if len(self._panel_cache) > 4:
                self._panel_cache.clear()
            self._panel_cache[key] = surf
        return self._panel_cache[key]

    def _draw_panel(self, surface, node):
        """绘制主对话面板和选项"""
        surface.blit(self._panel_surface(self.panel.size, (40, 40, 50, 220)),
                     self.panel.topleft)
        pygame.draw.rect(surface, (100, 100, 120), self.panel, 2)

        # 文本换行（按 (文本, 宽度) 缓存：同一节点每帧重画时不再逐字符测宽）
        text_lines = list(self._wrap_cached(node.text, self.panel.width - 40))
        max_lines = 8
        if len(text_lines) > max_lines:
            text_lines = text_lines[:max_lines]
            last = text_lines[-1]
            if len(last) > 3:
                text_lines[-1] = last[:-3] + "..."
            else:
                text_lines[-1] = "..."

        y = self.panel.y + 20
        line_height = self.font.get_height() + 2
        for line in text_lines:
            line = line.strip()
            text_surf = self._render_cached(line, self.font, (255, 255, 255))
            surface.blit(text_surf, (self.panel.x + 20, y))
            y += line_height

        # 选项按钮
        text_height = len(text_lines) * line_height
        option_start_y = self.panel.y + 20 + text_height + 20
        max_option_bottom = self.panel.bottom - 10
        option_height = 40
        option_spacing = 10

        max_options = min(
            len(node.options),
            (max_option_bottom - option_start_y) // (option_height + option_spacing)
        )
        if max_options < 0:
            max_options = 0

        self.option_buttons = []
        for i in range(max_options):
            y_pos = option_start_y + i * (option_height + option_spacing)
            if y_pos + option_height > max_option_bottom:
                break
            rect = pygame.Rect(self.panel.x + 50, y_pos,
                               self.panel.width - 100, option_height)
            self.option_buttons.append((rect, i))

        mouse_pos = pygame.mouse.get_pos()
        for rect, idx in self.option_buttons:
            if idx >= len(node.options):
                continue
            color = (80, 80, 120) if rect.collidepoint(mouse_pos) else (60, 60, 90)
            pygame.draw.rect(surface, color, rect, border_radius=5)
            option = node.options[idx]
            text = self._render_cached(option.text, self.font, (255, 255, 200))
            if text.get_width() > rect.width - 20:
                short_text = option.text[:10] + "..." if len(option.text) > 10 else option.text
                text = self._render_cached(short_text, self.font, (255, 255, 200))
            surface.blit(text, (rect.x + 10, rect.y + 8))

        if len(node.options) > len(self.option_buttons):
            hint = self._render_cached("... 更多选项", self.font, (200, 200, 200))
            surface.blit(hint, (self.panel.x + 50, max_option_bottom - 30))

    def _draw_popup(self, surface):
        """绘制材料需求弹窗（包含提交/取消按钮）"""
        # 弹窗位置在面板中央偏右
        if self.popup_rect is None:
            # 固定大小，居中
            popup_w = 400
            popup_h = 200
            x = self.panel.centerx - popup_w // 2
            y = self.panel.centery - popup_h // 2
            self.popup_rect = pygame.Rect(x, y, popup_w, popup_h)

        # 半透明遮罩（可选）不实现全局遮罩，直接画弹窗
        surface.blit(self._panel_surface(self.popup_rect.size, (30, 30, 40, 240)),
                     self.popup_rect.topleft)
        pygame.draw.rect(surface, (200, 200, 220), self.popup_rect, 2)

        # 标题
        title = self._render_cached("所需条件", self.popup_font, (255, 255, 200))
        surface.blit(title, (self.popup_rect.x + 20, self.popup_rect.y + 10))

        # 显示细节
        details = self.requirement_details
        y_offset = 50
        if details:
            if "items" in details:
                for item_id, required in details["items"].items():
                    current = self.player.inventory.get(item_id, 0)
                    text = f"{item_id} x {required}  (持有: {current})"
                    surf = self._render_cached(text, self.popup_font, (220, 220, 220))
                    surface.blit(surf, (self.popup_rect.x + 30, self.popup_rect.y + y_offset))
                    y_offset += 30
            if "level" in details:
                text = f"等级 >= {details['level']}"
                surf = self._render_cached(text, self.popup_font, (220, 220, 220))
                surface.blit(surf, (self.popup_rect.x + 30, self.popup_rect.y + y_offset))
                y_offset += 30
        else:
            surf = self._render_cached("（无特殊要求）", self.popup_font, (200, 200, 200))
            surface.blit(surf, (self.popup_rect.x + 30, self.popup_rect.y + y_offset))

        # 按钮
        submit_rect = pygame.Rect(self.popup_rect.x + 20, self.popup_rect.bottom - 60, 100, 40)
        cancel_rect = pygame.Rect(self.popup_rect.right - 120, self.popup_rect.bottom - 60, 100, 40)

        mouse_pos = pygame.mouse.get_pos()
        # 提交
        color_submit = (80, 180, 80) if submit_rect.collidepoint(mouse_pos) else (50, 120, 50)
        pygame.draw.rect(surface, color_submit, submit_rect, border_radius=5)
        text_sub = self._render_cached("提交", self.popup_font, (255, 255, 255))
        surface.blit(text_sub, (submit_rect.x + 25, submit_rect.y + 8))

        # 取消
        color_cancel = (180, 80, 80) if cancel_rect.collidepoint(mouse_pos) else (120, 50, 50)
        pygame.draw.rect(surface, color_cancel, cancel_rect, border_radius=5)
        text_can = self._render_cached("取消", self.popup_font, (255, 255, 255))
        surface.blit(text_can, (cancel_rect.x + 25, cancel_rect.y + 8))

    # ---------- 外部调用接口（由 World 调用） ----------
    def open_popup(self, option_index, requirement_details):
        """打开弹窗，显示材料需求"""
        self.popup_active = True
        self.pending_option_idx = option_index
        self.requirement_details = requirement_details
        self.popup_rect = None  # 重置位置，下次绘制重新居中

    def close_popup(self):
        """关闭弹窗（确认或取消后调用）"""
        self.popup_active = False
        self.pending_option_idx = -1
        self.requirement_details = {}

    # ---------- 辅助 ----------
    def _wrap_text(self, text, max_width):
        words = text.split(' ')
        lines = []
        current_line = []
        for word in words:
            if self.font.size(word)[0] > max_width:
                chars = list(word)
                for ch in chars:
                    test_line = ''.join(current_line) + ch
                    if self.font.size(test_line)[0] > max_width:
                        if current_line:
                            lines.append(''.join(current_line))
                        current_line = [ch]
                    else:
                        current_line.append(ch)
                continue
            if current_line:
                test_line = ' '.join(current_line + [word])
            else:
                test_line = word
            if self.font.size(test_line)[0] > max_width:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        if current_line:
            lines.append(' '.join(current_line))
        if not lines:
            lines = [text]
        lines = [line.strip() for line in lines]
        return lines

    def update_player(self, player):
        self.player = player