import pygame
import serializer

@serializer.register
class Item:
    def __init__(self, item_id, name, icon_path, stackable=True, quantity=1, description=""):
        self.id = item_id
        self.name = name
        try:
            self.icon = pygame.image.load(icon_path)
        except TypeError:
            self.icon = None
        self.stackable = stackable  # 是否可堆叠
        self.quantity = quantity  # 当前数量
        self.description = description

    def draw_icon(self, surface, pos, size=32):
        if not self.icon: return
        scaled_icon = pygame.transform.scale(self.icon, (size, size))
        surface.blit(scaled_icon, pos)