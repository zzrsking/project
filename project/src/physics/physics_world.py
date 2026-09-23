# src/physics/physics_world.py
import math
import pymunk
import pygame
from pygame import pypm

from src.physics.shape_factory import create_shapes_from_descriptions, calculate_moment_for_shapes

class PhysicsWorld:
    def __init__(self, gravity=(0, 0)):
        self.space = pymunk.Space()
        self.space.gravity = gravity


        # ---------- 关键参数调优：防止穿透 ----------
        self.space.iterations = 10
        self.space.collision_slop = 1.0
        self.space.collision_bias = 0.0
        self.space.damping = 0.99

        # 存储 GameObject -> (body, shapes) 的映射
        self.body_map = {}
        # 反向映射：body -> GameObject（用于引力计算时查找对象）
        self.body_to_obj = {}
        # 存储每个对象的引力质量（与物理质量分离）
        self.grav_mass_map = {}

        self._setup_collision_handlers()

    def _setup_collision_handlers(self):
        """可选的碰撞回调，用于检测碰撞事件（如攻击）"""
        pass

    # ---------- 引力质量计算（与尺寸成正比） ----------
    def _compute_gravitational_mass(self, obj, shape):
        """
        根据形状计算引力质量。
        你可以通过 obj 获取自定义密度（例如 obj.density），否则使用全局默认密度。
        """
        # 默认密度（可改为从 obj 读取，比如 obj.density）
        DENSITY = 1.0

        if isinstance(shape, pymunk.Circle):
            radius = shape.radius
            # 按三维球体：质量 ∝ r³（你可以改为 r² 来模拟二维圆盘）
            mass = DENSITY * 4 * math.pi * (radius ** 2)
            return mass

        elif isinstance(shape, pymunk.Poly):
            # 计算多边形面积（Pymunk 的 Poly 有 area 属性）
            area = shape.area
            mass = DENSITY * area
            return mass


        elif isinstance(shape, pymunk.Segment):
            # 线段视为无质量，或按长度
            return 0.0

        else:
            # 未知形状，返回 1.0 作为默认
            return 1.0

    # ---------- 物体添加 / 移除 ----------
    def add_object(self, obj, is_static=False):
        """统一添加接口，自动判断类型创建物理体，并存入映射"""
        if obj in self.body_map:
            return self.body_map[obj]

        if not obj.is_collidable:
            return None

        if hasattr(obj, 'collision_shapes') and obj.collision_shapes:
            # 使用自定义形状描述
            if is_static:
                body = pymunk.Body(body_type=pymunk.Body.STATIC)
            else:
                mass = 1  # 物理质量，可以根据需要调整
                moment = calculate_moment_for_shapes(obj.collision_shapes, mass)
                body = pymunk.Body(mass, moment)
                body.position = obj.world_position.center

            shapes = create_shapes_from_descriptions(body, obj.collision_shapes)
            for shape in shapes:
                shape.user_data = obj
            self.space.add(body, *shapes)
            self.body_map[obj] = (body, shapes)
            self.body_to_obj[body] = obj
            # 计算并存储引力质量（这里取第一个形状，或者可合并多个形状的质量）
            # 如果多个形状，可以求和，这里简化：只取第一个
            if shapes:
                self.grav_mass_map[obj] = self._compute_gravitational_mass(obj, shapes[0])
            else:
                self.grav_mass_map[obj] = 1.0
            return body, shapes

        # 原有逻辑：根据对象类型创建简单碰撞体
        if hasattr(obj, 'wall_type'):
            body, shape = self._create_wall_body(obj)
        elif hasattr(obj, 'inventory'):
            radius = max(obj.rect.width, obj.rect.height) / 2 * 0.75
            body, shape = self._create_circle_body(obj.world_position.center, radius, is_static)
        elif hasattr(obj, 'dialogue_tree'):  # NPC
            body, shape = self._create_box_body(obj.world_position, is_static)
        else:
            body, shape = self._create_box_body(obj.world_position, is_static)

        shape.user_data = obj
        self.body_map[obj] = (body, shape)
        self.body_to_obj[body] = obj
        # 计算引力质量
        self.grav_mass_map[obj] = self._compute_gravitational_mass(obj, shape)
        return body, shape

    def remove_object(self, obj):
        """从物理空间移除对象"""
        if obj not in self.body_map:
            return
        body, shape = self.body_map.pop(obj)
        self.space.remove(body, shape)
        del self.body_to_obj[body]
        if obj in self.grav_mass_map:
            del self.grav_mass_map[obj]

    def remove_all(self):
        """
        移除 pymunk Space 中的所有物体（刚体、形状、约束）。
        顺序：先移除约束，再移除形状，最后移除刚体，确保安全。
        """
        # 1. 复制当前所有物体的列表（避免迭代时修改原集合）
        constraints = list(self.space.constraints)
        shapes = list(self.space.shapes)
        bodies = list(self.space.bodies)

        # 2. 按依赖顺序移除
        # 约束依赖刚体，所以最先移除
        for c in constraints:
            self.space.remove(c)

        # 形状也依赖刚体，在刚体之前移除
        for s in shapes:
            self.space.remove(s)

        # 最后移除刚体
        for b in bodies:
            self.space.remove(b)

    # ---------- 访问器 ----------
    def get_body(self, obj):
        if obj in self.body_map:
            return self.body_map[obj][0]
        return None

    def set_velocity(self, obj, velocity):
        body = self.get_body(obj)
        if body and body.body_type != pymunk.Body.STATIC:
            body.velocity = velocity

    def teleport(self, obj, new_pos):
        body = self.get_body(obj)
        if body:
            body.position = new_pos
            body.velocity = (0, 0)

    # ---------- 物理步进与同步 ----------
    def step(self, dt):
        dt_fixed = 1.0 / 60.0
        steps = max(1, int(dt / dt_fixed))
        sub_dt = dt / steps
        for _ in range(steps):
            self.space.step(sub_dt)

    def sync_positions(self):
        """将物理引擎的位置同步回所有 GameObject 的 world_position"""
        for obj, (body, shape) in self.body_map.items():
            if body.body_type == pymunk.Body.STATIC:
                # 现在 body.position 已经是几何中心，直接同步即可（兼容墙体位置变化）
                body.position = pymunk.Vec2d(*obj.world_position.center)
            # 动态体同步
            if isinstance(shape, pymunk.Circle):
                obj.world_position.center = body.position
            elif isinstance(shape, pymunk.Poly):
                obj.world_position.center = body.position
            # 其他形状可扩展

    # ---------- 引力计算（使用独立引力质量） ----------
    def get_gravitational_force(self, body1, body2):
        """
        计算两个物理体之间的引力（矢量）。
        使用预先存储的引力质量，忽略 body.mass。
        返回值：pymunk.Vec2d 力向量。
        """
        # 通过 body 查找对应的游戏对象
        obj1 = self.body_to_obj.get(body1)
        obj2 = self.body_to_obj.get(body2)
        if obj1 is None or obj2 is None:
            return pymunk.Vec2d(0, 0)

        m1 = self.grav_mass_map.get(obj1, 1.0)
        m2 = self.grav_mass_map.get(obj2, 1.0)

        direction = body2.position - body1.position
        distance = direction.length
        if distance == 0:
            return pymunk.Vec2d(0, 0)

        G = .05  # 引力常数，可调整
        F_mag = G * m1 * m2 / (distance * distance)
        unit = direction / distance
        return F_mag * unit

    # ---------- 其他辅助 ----------
    def is_on_floor(self, body, check_distance=5):
        shapes = list(body.shapes)
        if not shapes:
            return False
        shape = shapes[0]
        if isinstance(shape, pymunk.Circle):
            foot_x = body.position.x
            foot_y = body.position.y + shape.radius
        else:
            aabb = shape.bb
            foot_x = body.position.x
            foot_y = aabb.bottom
        start = (foot_x, foot_y)
        end = (foot_x, foot_y + check_distance)
        filter = pymunk.ShapeFilter()
        query = self.space.segment_query(start, end, 0, filter)
        for hit in query:
            if hit.shape != shape:
                return True
        return False

    # ---------- 私有创建方法（方案二：顶点相对 body.position） ----------
    def _create_wall_body(self, wall, auto_add=True):
        """创建墙体物理体，顶点相对于 body.position，且 body.position 设为几何中心"""
        body = pymunk.Body(body_type=pymunk.Body.STATIC)

        if wall.wall_type == 'circle':
            center, radius = wall._points   # center 世界坐标 (cx, cy)
            body.position = center
            offset = (0, 0)
            shape = pymunk.Circle(body, radius, offset=offset)

        elif wall.wall_type == 'rectangle':
            pos, size = wall._points        # pos=(x,y) 左上角，size=(w,h)
            x, y = pos
            w, h = size
            cx, cy = x + w/2, y + h/2
            body.position = (cx, cy)
            half_w, half_h = w/2, h/2
            vertices = [
                (-half_w, -half_h),
                ( half_w, -half_h),
                ( half_w,  half_h),
                (-half_w,  half_h)
            ]
            shape = pymunk.Poly(body, vertices)

        elif wall.wall_type == 'polygon':
            abs_points = wall._points        # 绝对坐标顶点列表
            cx = sum(p[0] for p in abs_points) / len(abs_points)
            cy = sum(p[1] for p in abs_points) / len(abs_points)
            body.position = (cx, cy)
            rel_points = [(px - cx, py - cy) for px, py in abs_points]
            shape = pymunk.Poly(body, rel_points)

        else:
            raise ValueError(f"Unsupported wall type: {wall.wall_type}")

        shape.elasticity = 0.0
        shape.friction = 100
        shape.collision_type = 1

        if auto_add:
            self.space.add(body, shape)

        return body, shape

    def _create_circle_body(self, center, radius, is_static):
        mass = pymunk.inf if is_static else 1.0
        moment = pymunk.moment_for_circle(mass, 0, radius)
        body = pymunk.Body(mass, moment)
        body.position = center
        shape = pymunk.Circle(body, radius)
        shape.elasticity = 0.9
        shape.friction = 10
        shape.collision_type = 2
        self.space.add(body, shape)
        return body, shape

    def _create_box_body(self, rect, is_static):
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        cx, cy = x + w / 2, y + h / 2
        half_w, half_h = w / 2, h / 2
        vertices = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
        mass = pymunk.inf if is_static else 4.0
        moment = pymunk.moment_for_poly(mass, vertices)
        body = pymunk.Body(mass, moment)
        body.position = (cx, cy)
        shape = pymunk.Poly(body, vertices)
        shape.elasticity = 1.0
        shape.friction = 10
        shape.collision_type = 3
        self.space.add(body, shape)
        return body, shape

    def apply_gravity_between(self, body_a, body_b, dt):
        """计算并施加 body_a 和 body_b 之间的引力（仅对动态体）"""
        force = self.get_gravitational_force(body_a, body_b)
        if body_a.body_type != pymunk.Body.STATIC:
            body_a.velocity += force * dt / body_a.mass
        if body_b.body_type != pymunk.Body.STATIC:
            body_b.velocity -= force * dt / body_b.mass