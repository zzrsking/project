import pygame
import math
import time
import random



class Camera:
    def __init__(self, smoothness=4.0):
        # 核心状态：屏幕中心对应的世界坐标（当前 & 目标）
        self._x = 0.0
        self._y = 0.0
        self._target_x = 0.0
        self._target_y = 0.0

        # 缩放（当前 & 目标）
        self._zoom = 1.0
        self._target_zoom = 1.0

        # 屏幕参数
        self.screen_center = (0, 0)
        self.screen_size = (0, 0)

        # 绑定对象（用于锁定模式）
        self.bind_object = None
        # 新增：是否允许强制跟随绑定对象（外部可动态开关）
        self.follow_enabled = True

        # 插值参数
        self.lerp_speed = 0.1
        self.smoothness = smoothness

        # 缩放范围
        self.min_zoom = 0.1
        self.max_zoom = 5.0

        # 标识
        self.name = f"{int(time.time())}{random.randint(0, 1000):04d}"

    # -------------------- 对外暴露的属性（兼容原接口） --------------------
    @property
    def offset(self):
        cx, cy = self.screen_center
        return [self._x - cx / self._zoom, self._y - cy / self._zoom]

    @offset.setter
    def offset(self, value):
        cx, cy = self.screen_center
        self._x = value[0] + cx / self._zoom
        self._y = value[1] + cy / self._zoom

    @property
    def target_offset(self):
        cx, cy = self.screen_center
        return [self._target_x - cx / self._target_zoom,
                self._target_y - cy / self._target_zoom]

    @target_offset.setter
    def target_offset(self, value):
        cx, cy = self.screen_center
        self._target_x = value[0] + cx / self._target_zoom
        self._target_y = value[1] + cy / self._target_zoom

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        self._x = value

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        self._y = value

    @property
    def zoom(self):
        return self._zoom

    @zoom.setter
    def zoom(self, value):
        self._zoom = max(self.min_zoom, min(self.max_zoom, value))

    @property
    def target_zoom(self):
        return self._target_zoom

    @target_zoom.setter
    def target_zoom(self, value):
        self._target_zoom = max(self.min_zoom, min(self.max_zoom, value))

    # -------------------- 核心方法 --------------------
    def set_screen_size(self, w, h):
        """更新屏幕尺寸，保持左上角对应的世界坐标不变"""
        old_off_x = self._x - self.screen_center[0] / self._zoom
        old_off_y = self._y - self.screen_center[1] / self._zoom
        old_tgt_off_x = self._target_x - self.screen_center[0] / self._target_zoom
        old_tgt_off_y = self._target_y - self.screen_center[1] / self._target_zoom

        self.screen_center = (w // 2, h // 2)
        self.screen_size = (w, h)

        self._x = old_off_x + self.screen_center[0] / self._zoom
        self._y = old_off_y + self.screen_center[1] / self._zoom
        self._target_x = old_tgt_off_x + self.screen_center[0] / self._target_zoom
        self._target_y = old_tgt_off_y + self.screen_center[1] / self._target_zoom

    def update(self, delta_time, camera_lock):
        if delta_time <= 0:
            return

        # 锁定模式：仅当 follow_enabled 为 True 且 camera_lock 为 True 时，强制目标中心为绑定对象位置
        if camera_lock and self.follow_enabled and self.bind_object is not None:
            if hasattr(self.bind_object, 'world_position'):
                cx = self.bind_object.world_position.centerx
                cy = self.bind_object.world_position.centery
            else:
                cx, cy = self.bind_object.world_position.center
            self._target_x = cx
            self._target_y = cy

        # Lerp 因子
        lerp_factor = min(1.0, self.lerp_speed * delta_time * 60)

        # 插值中心点
        self._x += (self._target_x - self._x) * lerp_factor
        self._y += (self._target_y - self._y) * lerp_factor

        # 插值缩放
        self._zoom += (self._target_zoom - self._zoom) * lerp_factor
        self._zoom = max(self.min_zoom, min(self.max_zoom, self._zoom))

        # 误差修正（等效于原版修正 offset）
        cur_off = self.offset
        tgt_off = self.target_offset
        if abs(cur_off[0] - tgt_off[0]) < 0.001:
            self._x = tgt_off[0] + self.screen_center[0] / self._zoom
        if abs(cur_off[1] - tgt_off[1]) < 0.001:
            self._y = tgt_off[1] + self.screen_center[1] / self._zoom
        if abs(self._zoom - self._target_zoom) < 0.0001:
            self._zoom = self._target_zoom

    def set_zoom(self, target_zoom, update_offset=True):
        target_zoom = max(self.min_zoom, min(self.max_zoom, target_zoom))
        if update_offset:
            # 保持当前中心点不变
            self._target_x = self._x
            self._target_y = self._y
        else:
            # 保持目标偏移不变
            cur_tgt_off = self.target_offset
            self._target_x = cur_tgt_off[0] + self.screen_center[0] / target_zoom
            self._target_y = cur_tgt_off[1] + self.screen_center[1] / target_zoom
        self._target_zoom = target_zoom

    def zoom_at_center(self, delta):
        factor = 1.0 + delta * 0.1
        new_zoom = self._target_zoom * factor
        self.set_zoom(new_zoom, update_offset=True)

    def move(self, dx, dy):
        self._target_x += dx
        self._target_y += dy

    # -------------------- 坐标转换 --------------------
    def world_to_screen(self, world_pos):
        wx, wy = world_pos
        cx, cy = self.screen_center
        return (cx + (wx - self._x) * self._zoom,
                cy + (wy - self._y) * self._zoom)

    def screen_to_world(self, screen_pos):
        sx, sy = screen_pos
        cx, cy = self.screen_center
        return (self._x + (sx - cx) / self._zoom,
                self._y + (sy - cy) / self._zoom)

    # -------------------- 其他接口 --------------------
    def change_bind(self, obj):
        if self.bind_object is not None:
            try:
                self.bind_object.bind_camera = None
            except AttributeError:
                pass
        self.bind_object = obj
        try:
            obj.bind_camera = self
        except AttributeError:
            pass

    def get_viewport(self):
        return pygame.Rect(
            *self.offset,
            self.screen_size[0] / self._zoom,
            self.screen_size[1] / self._zoom
        )

    def get_view_matrix(self):
        return None