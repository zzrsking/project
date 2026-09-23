# setup.py
from src.utils.helpers import print_info
from settings import SCREEN_SIZE, ACTIVE_ZONE_SIZE
import os
from typing import Dict, List, Tuple, Optional, Any

import json
import pytmx
import pygame
from src.dialogue.tree import DialogueNode, DialogueOption
from src.core.resource_manager import get_resource_path, load_tileset, get_id_from_surface, get_surface_by_id, split_grid
from src.core import resource_manager
from src.utils.layer_v2 import LayerSpec, dataclasses_replace
from src.game.entity import Tiled, GameObject


# 在setup.py中添加保存相关函数
def save_level_data(data, filename="savegame.json"):
    """保存关卡数据到文件"""
    save_path = get_resource_path(filename)
    try:
        with open(save_path, 'w') as f:
            json.dump(data, f, indent=2)
        print_info(f"成功保存到 {save_path}")
    except Exception as e:
        print_info(f"保存失败: {str(e)}")

def load_level_data(filename="savegame.json"):
    """从文件加载关卡数据"""
    load_path = get_resource_path(filename)
    try:
        with open(load_path, 'r') as f:
            content = f.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                print_info(f"JSON 解析错误: {e}")
                print_info(f"错误位置: 行 {e.lineno}, 列 {e.colno}")

                # 显示错误位置附近的上下文
                start = max(0, e.pos - 20)
                end = min(len(content), e.pos + 20)
                context = content[start:end].replace("\n", "\\n")
                print_info(f"附近内容: ...{context}...")
            return data
    except Exception as e:
        print_info(f"加载失败: {str(e)}")
    return None


def save_dialogue_to_json(dialogue_tree: Dict[str, DialogueNode]):
    """
    将对话树保存为JSON文件（适配当前Dialogue类结构）

    参数:
        dialogue_tree: {node_id: DialogueNode} 字典
        filename: 输出JSON文件路径
    """

    def serialize_option(option: DialogueOption) -> Dict[str, Any]:
        """序列化单个选项"""
        return {
            "text": option.text,
            "next_node": option.next_node,
            "conditions": option.conditions,
            "effects": option.effects
        }

    serialized = {}
    for node_id, node in dialogue_tree.items():
        node_data = {
            "id": node.id,
            "text": node.text,
            "options": [serialize_option(opt) for opt in node.options]
        }
        serialized[node_id] = node_data
    return serialized


def load_dialogue_from_json(dialogue_tree_origin) -> Dict[str, DialogueNode]:
    """
    从JSON文件加载对话树（完全适配当前Dialogue类）

    参数:
        dialogue_tree_origin

    返回:
        重建的 {node_id: DialogueNode} 字典
    """

    dialogue_tree = {}
    for node_id, node_data in dialogue_tree_origin.items():
        options = []
        for opt_data in node_data["options"]:
            options.append(DialogueOption(
                text=opt_data["text"],
                next_node=opt_data["next_node"],
                conditions=opt_data.get("conditions", {}),
                effects=opt_data.get("effects", {})
            ))

        dialogue_tree[node_id] = DialogueNode(
            node_id=node_data["id"],
            text=node_data["text"],
            options=options
        )

    return dialogue_tree

# ---------------------------------------------------------------------------
# 地图瓦片来源：让地形在存档里只占"哪张地图 + 第几号"，而不是整块像素
# ---------------------------------------------------------------------------
_TMX_TILE_CACHE: Dict[str, Any] = {}


def _load_map_tile(map_path: str, gid: int):
    """给序列化器用的"按 (地图, gid) 取贴图"加载器（返回的正是 pytmx 缓存的对象）"""
    tmx = _TMX_TILE_CACHE.get(map_path)
    if tmx is None:
        try:
            tmx = pytmx.load_pygame(get_resource_path(map_path))
        except Exception:
            return None
        _TMX_TILE_CACHE[map_path] = tmx
    try:
        return tmx.get_tile_image_by_gid(int(gid))
    except Exception:
        return None


def _register_tile_source(tmx_data, surf, gid) -> None:
    """把一块地块贴图登记成"来自某张地图的某个 gid"（幂等，失败不影响加载）"""
    try:
        map_path = getattr(tmx_data, "dsh_source_path", None)
        if not map_path or surf is None:
            return
        resource_manager.register_map_tile_source(surf, map_path, gid)
    except Exception:
        pass


def load_tmx_data(path: str):
    tmx = pytmx.load_pygame(get_resource_path(path))
    try:
        tmx.dsh_source_path = resource_manager.normalize_path(path)
        _TMX_TILE_CACHE[tmx.dsh_source_path] = tmx
        resource_manager.register_map_tile_loader(_load_map_tile)
    except Exception:
        pass
    return tmx

def get_tmx_layers(tmx_data: pytmx.TiledMap) -> List[LayerSpec]:
    """
    从 TiledMap 中提取所有命名图层，生成 LayerSpec（Layer v2，声明式、不注册全局）。
    每层 kind/visible/opacity 由自定义属性或名称推断（见 LayerSpec.from_tmx）；
    order 优先取自定义属性 order，否则取 TMX 层序号（保证地图层相对顺序稳定）。
    """
    layers = []
    for idx, tmx_layer in enumerate(tmx_data.layers):
        name = tmx_layer.name
        if not name:
            continue
        props = tmx_layer.properties

        spec = LayerSpec.from_tmx(tmx_layer)
        order = props.get('order', None)
        if order is None:
            spec = dataclasses_replace(spec, order=idx)
        layers.append(spec)

    return layers

def get_tmx_all_layers_data(tmx_data: pytmx.TiledMap) -> Dict[str, Dict[str, Any]]:
    """提取所有图层数据"""
    result = {}

    for layer in tmx_data.layers:
        layer_info = {
            "visible": layer.visible,
            "opacity": getattr(layer, 'opacity', 1.0)
        }

        if isinstance(layer, pytmx.TiledTileLayer):
            tiles = []
            for x, y, gid in layer:
                surf = tmx_data.get_tile_image_by_gid(gid)
                if surf is None:
                    continue
                if not isinstance(surf, pygame.Surface):
                    print(f"警告：gid {gid} 不是 Surface，类型 {type(surf)}，跳过")
                    continue
                px = x * tmx_data.tilewidth + getattr(layer, 'offsetx', 0)
                py = y * tmx_data.tileheight + getattr(layer, 'offsety', 0)
                tiles.append((surf, (px, py)))
            layer_info["type"] = "tilelayer"
            layer_info["data"] = tiles

        elif isinstance(layer, pytmx.TiledObjectGroup):
            objects = []
            for obj in layer:
                obj_data = {
                    "id": obj.id,
                    "name": obj.name,
                    "type": obj.type,
                    "x": obj.x,
                    "y": obj.y,
                    "width": obj.width,
                    "height": obj.height,
                    "rotation": getattr(obj, 'rotation', 0),
                    "properties": obj.properties,
                }
                if hasattr(obj, 'points') and obj.points:
                    obj_data["points"] = obj.points
                objects.append(obj_data)
            layer_info["type"] = "objectlayer"
            layer_info["data"] = objects

        elif isinstance(layer, pytmx.TiledImageLayer):
            img_surf = getattr(layer, 'image', None)
            if img_surf is not None and isinstance(img_surf, pygame.Surface):
                px = getattr(layer, 'offsetx', 0)
                py = getattr(layer, 'offsety', 0)
                layer_info["data"] = [(img_surf, (px, py))]
            else:
                layer_info["data"] = []
            layer_info["type"] = "imagelayer"

        else:
            continue

        result[layer.name] = layer_info

    return result

def load_tmx_to_world(tmx_data: pytmx.TiledMap, world) -> None:
    """
    统一加载 TMX 地形数据到世界
    - 瓦片图层 -> Tiled 精灵（用于静态地图）
    - 对象图层/图像图层 -> GameObject（可用于动态物体、装饰或碰撞体）
    :param tmx_data:  pytmx 加载的地图数据
    :param world:     Tiled 类实例（需具备 add_game_object、physics 等）
    """
    for layer_index, layer in enumerate(tmx_data.layers):
        if not layer.visible:
            continue

        # ---- 0. 先按 TMX 声明建好这一层的 spec，并给无名层一个稳定的名字 ----
        # 两个坑一起补掉：
        #   ① 不建 spec 的话，world._ensure_layer 会按"层名猜 kind"自动建一个
        #      source=None 的层 —— 它看起来"不是地图来的"，场景存档会把它的对象也存下来，
        #      读档时地形重建又铺一遍，于是对象重复。
        #   ② 地图里有 11 个**无名** TiledObjectGroup；add_game_object(obj, None) 会被
        #      兜底成 "default"，把地图内容混进引擎自己的 default 层（同样导致重复，
        #      而且 default 层从此再也分不清哪些是地图带来的）。
        # layer_name = layer.name or f"tmx_layer_{layer_index}"
        if layer.name:
            layer_name = layer.name
        else:
            continue
        try:
            if layer_name not in world.layers:
                spec = LayerSpec.from_tmx(layer)
                spec = dataclasses_replace(
                    spec,
                    name=layer_name,
                    floor=getattr(spec, "floor", 0),
                    source=f"tmx:{layer_name}",
                )
                world.layers.add_layer(spec)
        except Exception:
            pass

        # ---- 1. 瓦片图层：使用 Tiled ----
        if isinstance(layer, pytmx.TiledTileLayer):
            for x, y, gid in layer:
                surf = tmx_data.get_tile_image_by_gid(gid)
                if surf is None or not isinstance(surf, pygame.Surface):
                    continue
                _register_tile_source(tmx_data, surf, gid)

                px = x * tmx_data.tilewidth + getattr(layer, 'offsetx', 0)
                py = y * tmx_data.tileheight + getattr(layer, 'offsety', 0)
                center = (px + surf.get_width()//2, py + surf.get_height()//2)

                sprite = Tiled(center, surf)   # 瓦片用 Tiled 类

                tile_props = tmx_data.tile_properties.get(gid, {})
                sprite.is_collidable = tile_props.get("block", False) or tile_props.get("collidable", False)
                world.add_game_object(sprite, layer_name)

        # ---- 2. 对象图层：使用 GameObject ----
        elif isinstance(layer, pytmx.TiledObjectGroup):
            for obj in layer:
                if hasattr(obj, 'gid') and obj.gid:
                    surf = tmx_data.get_tile_image_by_gid(obj.gid)
                    if surf and isinstance(surf, pygame.Surface):
                        _register_tile_source(tmx_data, surf, obj.gid)
                        center = (obj.x + surf.get_width()//2, obj.y + surf.get_height()//2)
                        # 改用 GameObject
                        obj_sprite = GameObject(center=center, image=surf)
                        obj_sprite.is_collidable = obj.properties.get("collidable", False) or obj.properties.get("block", False)
                        world.add_game_object(obj_sprite, layer_name)
                else:
                    # 纯矩形碰撞体：也创建 GameObject（透明），或直接添加物理碰撞体
                    if obj.width > 0 and obj.height > 0:
                        dummy_surf = pygame.Surface((obj.width, obj.height), pygame.SRCALPHA)
                        dummy_surf.fill((0, 0, 0, 0))
                        center = (obj.x + obj.width//2, obj.y + obj.height//2)
                        # 改用 GameObject
                        obj_sprite = GameObject(center=center, image=dummy_surf)
                        obj_sprite.is_collidable = True
                        obj_sprite.inactive_block = True   # 可选标记
                        world.add_game_object(obj_sprite, layer_name)

        # ---- 3. 图像图层：使用 GameObject ----
        elif isinstance(layer, pytmx.TiledImageLayer):
            img_surf = getattr(layer, 'image', None)
            if img_surf and isinstance(img_surf, pygame.Surface):
                px = getattr(layer, 'offsetx', 0)
                py = getattr(layer, 'offsety', 0)
                center = (px + img_surf.get_width()//2, py + img_surf.get_height()//2)
                # 改用 GameObject
                bg_sprite = GameObject(center=center, image=img_surf)
                bg_sprite.is_collidable = False
                world.add_game_object(bg_sprite, layer_name)

        else:
            continue


load_tileset("terrain\\graphics\\environment\\House.png", splitter=split_grid(8, 8))
load_tileset("terrain\\graphics\\environment\\House Decoration.png", splitter=split_grid(9, 6))