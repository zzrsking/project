import pygame
from src.core.state import GameMode
from src.ui.elements.button import UIButton

class PresetPropertyEditor:
    """对象预设属性编辑器"""

    def __init__(self, screen_rect, tool, engine):
        self.tool = tool
        self.engine = engine
        self.panel = pygame.Rect(screen_rect.width // 2 - 250, 100, 500, 450)
        self.values = {}
        self.active_field = None
        self.scroll_y = 0

        # 按钮
        self.button_apply = UIButton(pygame.Rect(0, 0, 100, 40), "应用")
        self.button_close = UIButton(pygame.Rect(0, 0, 100, 40), "关闭")
        self.update_buttons_position()

        # 构建属性列表
        self.build_property_list()

    def build_property_list(self):
        """构建要显示的属性列表"""
        # 通用属性
        self.common_properties = [
            PresetProperty("Rotation", "float", 0, 0, 360),
            PresetProperty("Scale", "float", 1.0, 0.1, 5.0)
        ]

        # 特殊对象属性
        self.special_properties = []
        if self.tool['interact_type'] == 'portal':
            # 获取Portal类并调用其方法
            Portal = self.engine.object_classes.get('Portal')
            if Portal and hasattr(Portal, "tailor_attributes"):
                portal = Portal((0, 0))
                self.special_properties = portal.tailor_attributes()

        # 合并属性列表
        self.all_properties = self.common_properties + self.special_properties

        # 初始化值
        for prop in self.all_properties:
            key = prop.name.lower().replace(" ", "_")
            # 如果工具已有该属性值则使用，否则用默认值
            self.values[prop.name] = self.tool.get(key, prop.default)

    def update_buttons_position(self):
        """更新按钮位置"""
        self.button_apply.rect.bottomright = (self.panel.right - 10, self.panel.bottom - 10)
        self.button_close.rect.bottomright = (self.panel.right - 120, self.panel.bottom - 10)

    def draw(self, surface):
        """绘制编辑器界面"""
        # 半透明背景
        background = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        background.fill((0, 0, 0, 150))
        surface.blit(background, (0, 0))

        # 主面板
        pygame.draw.rect(surface, (40, 40, 50), self.panel, border_radius=8)
        pygame.draw.rect(surface, (100, 100, 150), self.panel, 2, border_radius=8)

        # 标题
        font = pygame.font.Font("汉仪润圆-75W.ttf", 28)
        title = font.render(f"{self.tool['name']} 预设属性", True, (220, 220, 255))
        surface.blit(title, (self.panel.x + 20, self.panel.y + 20))

        # 绘制属性列表 (带滚动)
        prop_height = 40
        visible_height = self.panel.height - 100
        max_scroll = max(0, (len(self.all_properties) * prop_height) - visible_height)

        # 属性区域背景
        content_rect = pygame.Rect(
            self.panel.x + 20,
            self.panel.y + 70,
            self.panel.width - 40,
            visible_height
        )
        pygame.draw.rect(surface, (30, 30, 40), content_rect, border_radius=5)

        # 绘制每个属性
        clip_rect = pygame.Rect(content_rect)
        surface.set_clip(clip_rect)

        y_offset = content_rect.y + self.scroll_y
        for i, prop in enumerate(self.all_properties):
            if y_offset + prop_height < content_rect.y or y_offset > content_rect.y + content_rect.height:
                y_offset += prop_height
                continue

            # 高亮当前选中
            if i == self.active_field:
                prop_rect = pygame.Rect(content_rect.x, y_offset - 5, content_rect.width, prop_height)
                pygame.draw.rect(surface, (70, 70, 90), prop_rect, border_radius=5)

            # 属性名称
            font = pygame.font.Font("汉仪润圆-75W.ttf", 20)
            label = font.render(prop.name + ":", True, (220, 220, 255))
            surface.blit(label, (content_rect.x + 10, y_offset))

            # 值显示区域
            value_rect = pygame.Rect(
                content_rect.x + 150,
                y_offset,
                content_rect.width - 160,
                30
            )
            pygame.draw.rect(surface, (50, 50, 70), value_rect, border_radius=3)

            # 显示当前值
            value = self.values[prop.name]
            value_text = self.format_value(prop, value)

            value_font = pygame.font.Font("汉仪润圆-75W.ttf", 18)
            value_surf = value_font.render(value_text, True, (255, 255, 255))
            surface.blit(value_surf, (value_rect.x + 5, value_rect.y + 5))

            # 特殊类型处理
            if prop.type == "dropdown":
                # 绘制下拉箭头
                pygame.draw.polygon(surface, (200, 200, 200), [
                    (value_rect.right - 15, value_rect.centery - 5),
                    (value_rect.right - 5, value_rect.centery - 5),
                    (value_rect.right - 10, value_rect.centery + 5)
                ])

            y_offset += prop_height

        surface.set_clip(None)

        # 滚动条
        if max_scroll > 0:
            scrollbar_height = visible_height * (visible_height / (len(self.all_properties) * prop_height))
            scrollbar_y = content_rect.y + (self.scroll_y / max_scroll) * (visible_height - scrollbar_height)
            scrollbar_rect = pygame.Rect(
                content_rect.right - 8,
                scrollbar_y,
                6,
                scrollbar_height
            )
            pygame.draw.rect(surface, (100, 100, 140), scrollbar_rect, border_radius=3)

        # 绘制按钮
        self.button_apply.draw(surface)
        self.button_close.draw(surface)

    def format_value(self, prop, value):
        """格式化值用于显示"""
        if prop.type == "color":
            return f"RGB: {value[0]},{value[1]},{value[2]}"
        elif prop.type == "float":
            return f"{value:.2f}"
        elif prop.type == "dropdown":
            return str(value)
        return str(value)

    def handle_events(self, event):
        """处理编辑器事件"""
        if not self.panel:
            return

        mouse_pos = pygame.mouse.get_pos()

        # 鼠标滚动
        if event.type == pygame.MOUSEWHEEL:
            prop_height = 40
            visible_height = self.panel.height - 100
            max_scroll = max(0, (len(self.all_properties) * prop_height) - visible_height)

            self.scroll_y -= event.y * 30
            self.scroll_y = max(0, min(self.scroll_y, max_scroll))
            return True

        if event.type == pygame.MOUSEBUTTONDOWN:
            # 检查按钮点击
            if self.button_apply.rect.collidepoint(mouse_pos):
                return "apply"
            elif self.button_close.rect.collidepoint(mouse_pos):
                return "close"

            # 检查属性字段点击
            content_rect = pygame.Rect(
                self.panel.x + 20,
                self.panel.y + 70,
                self.panel.width - 40,
                self.panel.height - 100
            )

            if content_rect.collidepoint(mouse_pos):
                y = mouse_pos[1] - content_rect.y + self.scroll_y
                index = y // 40

                if 0 <= index < len(self.all_properties):
                    self.active_field = index
                    return True

        # 处理键盘输入
        elif event.type == pygame.KEYDOWN and self.active_field is not None:
            prop = self.all_properties[self.active_field]
            current = self.values[prop.name]
            value_str = str(current)

            if event.key == pygame.K_ESCAPE:
                self.active_field = None
                return True

            prop = self.all_properties[self.active_field]
            value_str = str(self.values[prop.name])

            if event.key == pygame.K_BACKSPACE:
                value_str = value_str[:-1]
            elif event.key == pygame.K_RETURN:
                self.active_field = None
            elif event.unicode and (event.unicode.isprintable() and not event.unicode.isspace()):
                value_str += event.unicode

            # 转换并验证值
            try:
                new_value = None
                # 类型安全转换
                if prop.type == "float":
                    new_value = float(value_str)
                    new_value = max(prop.min, min(new_value, prop.max))
                elif prop.type == "int":
                    new_value = int(value_str)
                    new_value = max(prop.min, min(new_value, prop.max))
                elif prop.type == "color":
                    # 特殊颜色处理
                    if value_str.startswith("(") and value_str.endswith(")"):
                        parts = value_str[1:-1].split(",")
                        if len(parts) == 3:
                            r = int(parts[0])
                            g = int(parts[1])
                            b = int(parts[2])
                            new_value = (max(0, min(255, r)),
                                         max(0, min(255, g)),
                                         max(0, min(255, b)))
                elif prop.type == "bool":
                    # 布尔值特殊处理
                    new_value = value_str.lower() in ["true", "1", "yes", "t", "y"]
                else:
                    new_value = value_str

                self.values[prop.name] = new_value
            except ValueError:
                # 转换失败时恢复原值
                self.values[prop.name] = current