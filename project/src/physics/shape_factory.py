import pymunk
from pymunk import Vec2d

def create_shapes_from_descriptions(body, shapes_desc):
    """
    根据形状描述列表创建 Pymunk 形状列表。
    shapes_desc: 列表，每个元素为字典：
        {'type': 'circle', 'offset': (x,y), 'radius': r}
        {'type': 'rect', 'offset': (x,y), 'width': w, 'height': h}
        {'type': 'polygon', 'offset': (x,y), 'points': [(x1,y1), ...]}  # 相对于中心的偏移
    返回 shapes 列表。
    """
    shapes = []
    for desc in shapes_desc:
        typ = desc['type']
        offset = Vec2d(desc.get('offset', (0, 0)))
        if typ == 'circle':
            radius = desc['radius']
            shape = pymunk.Circle(body, radius, offset=offset)
        elif typ == 'rect':
            w = desc['width']
            h = desc['height']
            half_w = w / 2
            half_h = h / 2
            vertices = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
            vertices = [(v[0] + offset.x, v[1] + offset.y) for v in vertices]
            shape = pymunk.Poly(body, vertices)
        elif typ == 'polygon':
            points = desc['points']  # 相对于 offset
            points = [(p[0] + offset.x, p[1] + offset.y) for p in points]
            shape = pymunk.Poly(body, points)
        else:
            raise ValueError(f"Unknown shape type: {typ}")
        shape.elasticity = 1.0
        shape.friction = 0.9
        shapes.append(shape)
    return shapes


def calculate_moment_for_shapes(shapes_desc, mass):
    """计算多个形状的总转动惯量（围绕 body 中心）"""
    moment = 0
    for desc in shapes_desc:
        typ = desc['type']
        offset = Vec2d(desc.get('offset', (0, 0)))
        if typ == 'circle':
            r = desc['radius']
            # 使用 moment_for_circle，指定偏移
            moment += pymunk.moment_for_circle(mass, 0, r, offset=offset)
        elif typ == 'rect':
            w = desc['width']
            h = desc['height']
            half_w = w / 2
            half_h = h / 2
            vertices = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
            vertices = [(v[0] + offset.x, v[1] + offset.y) for v in vertices]
            moment += pymunk.moment_for_poly(mass, vertices)
        elif typ == 'polygon':
            points = desc['points']
            points = [(p[0] + offset.x, p[1] + offset.y) for p in points]
            moment += pymunk.moment_for_poly(mass, points)
    return moment


import pymunk
import math
from typing import List, Tuple


def get_body_collision_vertices(
        body: pymunk.Body,
        circle_segments: int = 20
) -> List[List[Tuple[float, float]]]:
    """
    传入一个已添加到世界中的 Body（物体），返回它所有碰撞箱的世界坐标顶点。

    参数:
        body: pymunk.Body 实例（必须已关联到 space）
        circle_segments: 圆形近似为多边形的边数（默认20，足够平滑）

    返回:
        List[List[Tuple[float, float]]]:
            一个列表，其中每个元素是一个形状的顶点列表，
            每个顶点是 (x, y) 世界坐标元组。
            例如: [ [(x1,y1), (x2,y2), ...], [(x3,y3), (x4,y4), ...] ]
            （如果是组合体，里面会有多个子列表，方便你分别绘制）
    """
    if body is None:
        return []

    all_shapes_vertices = []

    # 遍历该 Body 上挂载的所有形状（一个 Body 可以有多个 Shape）
    for shape in body.shapes:
        vertices = []

        # ---------- 1. 多边形 ----------
        if isinstance(shape, pymunk.Poly):
            for local_v in shape.get_vertices():
                world_v = body.local_to_world(local_v)
                vertices.append((world_v.x, world_v.y))

        # ---------- 2. 圆形 ----------
        elif isinstance(shape, pymunk.Circle):
            radius = shape.radius
            offset = shape.offset  # 圆心相对于 Body 局部原点的偏移
            for i in range(circle_segments):
                angle = 2.0 * math.pi * i / circle_segments
                # 计算局部坐标：偏移量 + 半径方向上的点
                local_x = offset.x + radius * math.cos(angle)
                local_y = offset.y + radius * math.sin(angle)
                world_v = body.local_to_world((local_x, local_y))
                vertices.append((world_v.x, world_v.y))

        # ---------- 3. 线段 ----------
        elif isinstance(shape, pymunk.Segment):
            a = body.local_to_world(shape.a)
            b = body.local_to_world(shape.b)
            vertices = [(a.x, a.y), (b.x, b.y)]

        # ---------- 如果有其他未知形状，跳过（不会报错） ----------
        else:
            # 可以加个警告，但为了稳定性，直接跳过
            continue

        # 只有顶点数量 >= 2 才保留（线段2个点，多边形至少3个，圆形至少3个）
        if len(vertices) >= 2:
            all_shapes_vertices.append(vertices)

    return all_shapes_vertices