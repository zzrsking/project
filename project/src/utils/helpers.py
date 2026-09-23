# helpers.py
import inspect
import threading
import functools
import traceback
import math
import json
import os

import pygame
import pytmx
from pygame import Vector2
from typing import Dict, Any
from pygame.image import load
import settings
from src.dialogue.tree import DialogueNode, DialogueOption


# ============================================================
# 原始工具函数
# ============================================================

def print_info(*args, trace_back=False, **kwargs):
    """打印调试信息，包含调用位置；trace_back=True 时额外打印完整堆栈。

    注意参数顺序：trace_back 现在是【关键字参数】。旧签名把它放在第一个位置参数上
    （`print_info(trace_back=False, *args, **kwargs)`），于是所有 "print_info(消息)" 的
    调用都把消息绑给了 trace_back —— 非空即真，每次调用都白打一整份堆栈，
    声明出来的默认值 trace_back=False 根本用不到（全项目 37 处调用都受影响）。
    """
    print(*args, **kwargs)
    frame = inspect.currentframe().f_back
    file = inspect.getframeinfo(frame).filename
    line = inspect.getframeinfo(frame).lineno
    print(f"调用位置:{file}|line:{line}")
    if trace_back:
        traceback.print_stack() # 非必要勿开启


def lerp_vector(v1: Vector2, v2: Vector2, t: float) -> Vector2:
    """向量线性插值"""
    return v1 + (v2 - v1) * t


def clamp(value, min_val, max_val):
    """限制数值范围"""
    return max(min_val, min(value, max_val))


def distance(p1, p2):
    """计算两点距离"""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def world_to_screen(world_pos, camera_offset, zoom):
    """世界坐标转屏幕坐标"""
    return ((world_pos[0] - camera_offset[0]) * zoom,
            (world_pos[1] - camera_offset[1]) * zoom)


def screen_to_world(screen_pos, camera_offset, zoom):
    """屏幕坐标转世界坐标"""
    return (screen_pos[0] / zoom + camera_offset[0],
            screen_pos[1] / zoom + camera_offset[1])


# ============================================================
# JSON 转换功能（返回可执行的对话树对象）
# ============================================================

def load_dialogue_as_objects(file_path: str) -> dict:
    """
    从 JSON 文件加载对话数据，转换为可执行的 DialogueNode 对象字典。

    返回值可以直接赋值给角色的 dialogue_tree 属性：
        player.dialogue_tree = load_dialogue_as_objects("dialogue.json")

    参数:
        file_path: JSON 文件路径

    返回:
        dict: {node_id: DialogueNode, ...}
    """
    if not os.path.exists(file_path):
        print_info(f"对话数据文件不存在:{file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    return _parse_dialogue_data(raw_data)


def load_dialogue_from_string(json_str: str) -> dict:
    """
    从 JSON 字符串解析对话数据，返回可执行的 DialogueNode 对象字典。
    """
    try:
        raw_data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"无效的 JSON 字符串: {e}")
    return _parse_dialogue_data(raw_data)


def load_dialogue_from_clipboard() -> dict:
    """
    从系统剪贴板读取 JSON，返回可执行的 DialogueNode 对象字典。
    需要安装 pyperclip: pip install pyperclip
    """
    try:
        import pyperclip
    except ImportError:
        raise ImportError("请先安装 pyperclip: pip install pyperclip")

    json_str = pyperclip.paste()
    if not json_str:
        raise ValueError("剪贴板为空")
    return load_dialogue_from_string(json_str)


def save_dialogue_as_json(dialogue_tree: dict, file_path: str) -> None:
    """
    将可执行的对话树对象保存为 JSON 文件。

    参数:
        dialogue_tree: {node_id: DialogueNode, ...}
        file_path: 输出 JSON 文件路径
    """
    export_data = {}
    for node_id, node in dialogue_tree.items():
        export_data[node_id] = {
            "text": node.text,
            "options": [
                {
                    "text": opt.text,
                    "next_node": opt.next_node,
                    "conditions": opt.conditions,
                    "effects": opt.effects
                }
                for opt in node.options
            ]
        }

    dirname = os.path.dirname(file_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)


# ============================================================
# 内部解析函数
# ============================================================

def _parse_dialogue_data(raw_data: dict) -> dict:
    """
    将原始 JSON 字典转换为 DialogueNode 对象字典。
    返回的字典可以直接赋值给角色.dialogue_tree。
    """
    result = {}
    for node_id, node_data in raw_data.items():
        options = []
        for opt in node_data.get("options", []):
            options.append(DialogueOption(
                text=opt.get("text", ""),
                next_node=opt.get("next_node", ""),
                condition_id=opt.get("condition_id", {}),
                effects=opt.get("effects", {})
            ))
        result[node_id] = DialogueNode(
            node_id=node_id,
            text=node_data.get("text", ""),
            options=options
        )
    return result


class UniqueList:
    def __init__(self, iterable=None):
        self._items = []
        self._index_map = {}
        if iterable:
            for item in iterable:
                self.append(item)

    def append(self, item):
        if item not in self._index_map:
            self._index_map[item] = len(self._items)
            self._items.append(item)

    def index(self, item):
        return self._index_map[item]  # O(1)

    def __getitem__(self, idx):
        return self._items[idx]

    def __contains__(self, item):
        return item in self._index_map  # O(1)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __repr__(self):
        return f"UniqueList({self._items})"


import pymunk
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

def threaded(func):
    """后台线程装饰器"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        thread.start()
    return wrapper
