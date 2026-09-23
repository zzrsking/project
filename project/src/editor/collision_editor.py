import pygame
from pygame import Vector2
import math
from src.physics.physics_world import PhysicsWorld

class CollisionEditor:
    def __init__(self, world):
        self.world = world
        self.active = False          # 是否进入碰撞编辑模式
        self.selected_obj = None     # 当前编辑的对象（由外部设置）
        self.dragging = False
        self.drag_shape_idx = None   # 正在拖拽的形状索引
        self.drag_point_idx = None   # 对于多边形是顶点索引，圆形是 0:圆心, 1:半径端点
        self.drag_start_pos = None
        self.original_desc = None    # 拖拽前的形状描述备份

        # 颜色配置
        self.color_outline = (0, 255, 0, 200)
        self.color_control = (255, 0, 0, 255)
        self.color_highlight = (255, 255, 0, 255)

    def set_selected_obj(self, obj):
        self.selected_obj = obj

    def toggle_active(self):
        self.active = not self.active
        if not self.active:
            self.selected_obj = None
            self.dragging = False

    def update(self):
        # 可选的更新逻辑
        pass

    def draw(self, surface, camera):
        if not self.active or not self.selected_obj:
            return
        obj = self.selected_obj
        if not hasattr(obj, 'collision_shapes'):
            return

        shapes_desc = obj.collision_shapes
        center = obj.world_position.center

        for idx, desc in enumerate(shapes_desc):
            typ = desc['type']
            offset = desc.get('offset', (0, 0))
            # 计算世界坐标
            if typ == 'circle':
                r = desc['radius']
                world_center = (center[0] + offset[0], center[1] + offset[1])
                screen_center = camera.world_to_screen(world_center)
                screen_radius = r * camera.zoom
                # 绘制圆轮廓
                pygame.draw.circle(surface, self.color_outline, screen_center, int(screen_radius), 2)
                # 绘制控制点：圆心和半径端点（右端点）
                pygame.draw.circle(surface, self.color_control, screen_center, 4)
                end_point = (world_center[0] + r, world_center[1])
                screen_end = camera.world_to_screen(end_point)
                pygame.draw.circle(surface, self.color_control, screen_end, 4)

            elif typ == 'rect' or typ == 'polygon':
                if typ == 'rect':
                    w = desc['width']; h = desc['height']
                    half_w = w/2; half_h = h/2
                    points = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
                else:
                    points = desc['points']   # 相对偏移
                # 应用偏移并转换到世界坐标
                world_points = [(center[0] + p[0] + offset[0], center[1] + p[1] + offset[1]) for p in points]
                screen_points = [camera.world_to_screen(p) for p in world_points]
                if len(screen_points) >= 3:
                    pygame.draw.polygon(surface, self.color_outline, screen_points, 2)
                # 绘制每个顶点控制点
                for pt in screen_points:
                    pygame.draw.circle(surface, self.color_control, pt, 4)

    def handle_event(self, event, camera):
        if not self.active or not self.selected_obj:
            return False
        obj = self.selected_obj
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # 检测是否点击到控制点
            mouse_pos = pygame.mouse.get_pos()
            world_pos = camera.screen_to_world(mouse_pos)
            center = obj.world_position.center
            shapes_desc = obj.collision_shapes

            # 遍历所有形状，检测点击
            for idx, desc in enumerate(shapes_desc):
                typ = desc['type']
                offset = desc.get('offset', (0, 0))
                if typ == 'circle':
                    r = desc['radius']
                    world_center = (center[0] + offset[0], center[1] + offset[1])
                    # 检测圆心
                    if math.hypot(world_pos[0]-world_center[0], world_pos[1]-world_center[1]) < 10/camera.zoom:
                        self.dragging = True
                        self.drag_shape_idx = idx
                        self.drag_point_idx = 0  # 圆心
                        self.drag_start_pos = world_pos
                        self.original_desc = desc.copy()
                        return True
                    # 检测半径端点（右端点）
                    end_point = (world_center[0] + r, world_center[1])
                    if math.hypot(world_pos[0]-end_point[0], world_pos[1]-end_point[1]) < 10/camera.zoom:
                        self.dragging = True
                        self.drag_shape_idx = idx
                        self.drag_point_idx = 1  # 半径端点
                        self.drag_start_pos = world_pos
                        self.original_desc = desc.copy()
                        return True
                elif typ == 'rect' or typ == 'polygon':
                    if typ == 'rect':
                        w = desc['width']; h = desc['height']
                        half_w = w/2; half_h = h/2
                        points = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
                    else:
                        points = desc['points']
                    world_points = [(center[0] + p[0] + offset[0], center[1] + p[1] + offset[1]) for p in points]
                    for pi, wp in enumerate(world_points):
                        if math.hypot(world_pos[0]-wp[0], world_pos[1]-wp[1]) < 10/camera.zoom:
                            self.dragging = True
                            self.drag_shape_idx = idx
                            self.drag_point_idx = pi
                            self.drag_start_pos = world_pos
                            self.original_desc = desc.copy()
                            return True
            return False

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging:
                self.dragging = False
                # 更新物理体
                self._rebuild_physics(obj)
                self.drag_shape_idx = None
                self.drag_point_idx = None
                self.original_desc = None
                return True
            return False

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging:
                mouse_pos = pygame.mouse.get_pos()
                world_pos = camera.screen_to_world(mouse_pos)
                # 根据拖拽类型更新形状描述
                desc = self.original_desc
                if desc is None:
                    return False
                # 获取当前形状描述
                current_desc = obj.collision_shapes[self.drag_shape_idx]
                typ = current_desc['type']
                offset = current_desc.get('offset', (0, 0))
                center = obj.world_position.center

                if typ == 'circle':
                    if self.drag_point_idx == 0:  # 拖拽圆心
                        new_center = world_pos
                        # 更新 offset
                        new_offset = (new_center[0] - center[0], new_center[1] - center[1])
                        current_desc['offset'] = new_offset
                    else:  # 拖拽半径端点
                        # 计算新半径
                        r = math.hypot(world_pos[0] - (center[0]+offset[0]), world_pos[1] - (center[1]+offset[1]))
                        if r > 0:
                            current_desc['radius'] = r
                elif typ == 'rect' or typ == 'polygon':
                    # 拖拽顶点
                    if typ == 'rect':
                        # 矩形拖拽顶点相当于修改宽高，但更简单方式：将矩形转为多边形处理
                        # 为了简化，我们只支持多边形拖拽，矩形暂时不支持顶点拖拽，可转为多边形
                        # 这里示范多边形处理
                        pass
                    else:  # polygon
                        # 更新对应顶点
                        points = current_desc['points']
                        # 计算相对偏移
                        new_point = (world_pos[0] - center[0] - offset[0], world_pos[1] - center[1] - offset[1])
                        points[self.drag_point_idx] = new_point
                        current_desc['points'] = points
                # 拖拽过程中的物理重建去掉了：轮廓是按 collision_shapes 直接画的，
                # 拖动时并不需要新的物理体；旧代码每个 MOUSEMOTION 都 remove+add 一遍
                # 刚体并 sync_positions（一帧内可能重建好几次，既卡又抖）。
                # 松手时（MOUSEBUTTONUP）统一重建一次即可。
                return True
            return False
        return False

    def _rebuild_physics(self, obj):
        """移除旧物理体并重新添加"""
        if obj in self.world.physics.body_map:
            self.world.physics.remove_object(obj)
        self.world.physics.add_object(obj)
        # 同步位置（如果对象是动态的）
        self.world.physics.sync_positions()

    def add_shape(self, shape_type='circle'):
        """添加一个默认形状"""
        if not self.selected_obj:
            return
        obj = self.selected_obj
        if shape_type == 'circle':
            new_shape = {'type': 'circle', 'offset': (0, 0), 'radius': 20}
        elif shape_type == 'rect':
            new_shape = {'type': 'rect', 'offset': (0, 0), 'width': 30, 'height': 20}
        elif shape_type == 'polygon':
            new_shape = {'type': 'polygon', 'offset': (0, 0), 'points': [(-20,-15), (20,-15), (20,15), (-20,15)]}
        else:
            return
        obj.collision_shapes.append(new_shape)
        self._rebuild_physics(obj)

    def delete_shape(self, index):
        if not self.selected_obj or index >= len(self.selected_obj.collision_shapes):
            return
        del self.selected_obj.collision_shapes[index]
        self._rebuild_physics(self.selected_obj)