import pygame

from game.camera import Camera
from src.ui.elements import theme
from src.ui.elements.button import UIButton, ButtonGroup
from src.ui.elements.fonts import get_font

class GameplayInterface:
    def __init__(self, screen_rect):
        self.screen_rect = screen_rect
        # self.mode_buttons = ButtonGroup()
        self._create_mode_buttons()
        self.status_bar = pygame.Rect(0, screen_rect.height - 30, screen_rect.width, 30)

    def _create_mode_buttons(self):
        # 与编辑器里那两颗保持同一套：主按钮实心强调色 + 次级按钮中性面
        width = self.screen_rect.width
        self.mode_buttons = [
            UIButton(pygame.Rect(width - 310, 12, 100, 36), "Editor", theme.PRIMARY,
                     opaque=True),
            UIButton(pygame.Rect(width - 200, 12, 100, 36), "Settings", theme.ACCENT,
                     opaque=True, filled=False),
        ]

    def update(self, mouse_pos):
        for btn in self.mode_buttons:
            btn.check_hover(mouse_pos)

    def draw(self, surface, camera, mouse_pos, active_player):
        for btn in self.mode_buttons:
            btn.draw(surface)
        pygame.draw.rect(surface, (50,50,70), self.status_bar)
        self._draw_world_hud(surface, active_player)
        self.draw_coordinates(surface, camera, mouse_pos)

    def draw_coordinates(self, surface, camera, mouse_pos):
        font = get_font(20, None)
        screen_coord = f"Screen: {mouse_pos[0]}, {mouse_pos[1]}"
        world_x, world_y = camera.screen_to_world(mouse_pos)
        world_coord = f"World: {world_x}, {world_y}"
        texts = [font.render(screen_coord, True, (255,255,255)), font.render(world_coord, True, (255,255,255))]
        x = self.status_bar.left + 10
        y = self.status_bar.top + 5
        for text in texts:
            surface.blit(text, (x, y))
            x += 20 + 100

    def _draw_world_hud(self, surface, active_player):
        """绘制游戏世界的 UI"""
        target = active_player.current_lock_target if active_player else None
        # 锁定目标显示 + 交互提示（两段都用同一个 target，不再重复判断/重复取属性）
        if target is not None:
            name = target.properties.get('name', 'Unknown')
            text = get_font(24).render(f"锁定: {name}", True, (255, 255, 255))
            surface.blit(text, (10, 50))

            prompt = ""
            if target.interact_type == "enemy":
                prompt = "[A] 攻击"
            elif target.interact_type in ["npc", "item"]:
                prompt = f"[F] 与{target.properties.get('name', '目标')}对话"
            elif target.interact_type == "portal":
                prompt = "[F] 进入传送门"

            if prompt:
                text = get_font(32).render(prompt, True, (255, 255, 0))
                pos = (surface.get_width() // 2 - 50, surface.get_height() - 100)
                surface.blit(text, pos)

        # 玩家物品栏
        if active_player is not None and hasattr(active_player, 'inventory'):
            try:
                active_player.inventory.draw(surface)
            except Exception:
                pass

    def handle_event(self, event):
        ...