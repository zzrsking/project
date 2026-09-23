import pygame

class Wall(pygame.sprite.Sprite):
    def __init__(self, wall_type, points):
        super().__init__()
        self.wall_type = wall_type
        self.points = points
        self.is_collidable = True
        self.in_active_block = False
        self.image = pygame.Surface((64, 64))

        if wall_type == 'rectangle':
            pos, size = points
            x, y = pos
            w, h = size
            self.world_position = pygame.Rect(x, y, w, h)
        elif wall_type == 'circle':
            center, radius = points
            cx, cy = center
            self.world_position = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
        elif wall_type == 'polygon':
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            self.world_position = pygame.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        else:
            self.world_position = pygame.Rect(0, 0, 64, 64)

    # 墙体无需图像，但可添加调试绘制