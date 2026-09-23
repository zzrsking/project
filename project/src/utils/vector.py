from pygame import rect


class Position(rect.Rect):
    """自定义位置类，继承自pygame.Rect"""
    def __init__(self, x, y, width=0, height=0):
        # 保留Rect的物理碰撞框特性，但默认宽高为0（纯坐标点）
        super().__init__(x, y, width, height)

    @property
    def center_position(self):
        """获取中心坐标 (x,y) """
        return self.center

    def copy(self):
        return Position(self.x, self.y, self.width, self.height)

    @center_position.setter
    def center_position(self, value):
        """通过中心坐标更新位置"""
        self.center = value

    def distance_to(self, target_position):
        P1 = self.center
        P2 = target_position.center
        return ((P1[0]-P2[0])**2 + (P1[1]-P2[1])**2)**.5
