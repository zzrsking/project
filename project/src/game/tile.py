import pygame

class Tile(pygame.sprite.Sprite):
    def __init__(self, image, x, y, width, height, layer_name=""):
        super().__init__()
        self.image = image
        self.rect = pygame.Rect(0, 0, width, height)
        self.world_position = pygame.Rect(x, y, width, height)
        self.layer_name = layer_name
        self.in_active_block = False
        self.is_collidable = False   # 普通瓦片不参与物理

    def update_rect(self, offset, zoom):
        self.rect.x = (self.world_position.x - offset[0]) * zoom
        self.rect.y = (self.world_position.y - offset[1]) * zoom
        self.rect.width = self.world_position.width * zoom
        self.rect.height = self.world_position.height * zoom