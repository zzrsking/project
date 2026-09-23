import pygame
from pygame import Vector2, surface

from src.game.entity import sunshiyi
from src.game.entity import GameObject, NPC, Player, Portal, ProceduralObject, LoadedImageObject
from src.core.resource_manager import load_image, get_resource_path, load_procedural_surface
from src.item.item import Item
from src.utils import helpers


def create_tile_object(tool, world_pos, interact_type="none"):
    image_path = f"{tool['image']}.png"
    image = load_image(get_resource_path(image_path))   # 不再自动计数
    obj = LoadedImageObject(image=image, center=world_pos, interact_type=interact_type)
    obj.is_collidable = False
    return obj

def create_collision_box_object(tool, world_pos, interact_type="none"):
    image = load_procedural_surface("placeholder", ())
    obj = ProceduralObject(image=image, center=world_pos,
                     interact_type=interact_type, is_collidable=True,
                     procedural_type="placeholder", procedural_params=())
    obj.dialogue_tree = helpers.load_dialogue_as_objects("assets/dialogue_tree.json")
    return obj

def create_character_object(world_pos):
    right_frames = load_image(
        get_resource_path("characters/default_player.png"),
        scale=(64, 64),
        frame_count=4,
        flip_x=False,
    )
    left_frames = load_image(
        get_resource_path("characters/default_player.png"),
        scale=(64, 64),
        frame_count=4,
        flip_x=True,
    )
    obj = Player(right_frames, left_frames, center=world_pos)
    obj.inventory.add_item(Item("gold", "金币", None, quantity=1000))
    obj.level = 100
    obj.is_collidable = True
    return obj

def create_portal_object(world_pos):
    P_surface = load_procedural_surface("portal", ())
    obj = Portal(surface=P_surface, center=world_pos)
    obj.properties["name"] = f"传送门_{pygame.time.get_ticks()}"
    obj.is_collidable = False
    return obj

def create_sunshiyi_object(tool, world_pos):
    image_path = f"{tool['image']}.png"
    image = load_image(get_resource_path(image_path), scale=(256, 256))
    obj = sunshiyi(image=image, center=world_pos)
    obj.properties["name"] = "孙石毅"
    obj.is_collidable = True
    return obj

def create_object_from_tool(tool, world_pos):
    """
    根据编辑器工具描述创建对象实例（统一入口函数）
    """
    tool_type = tool.get('type')
    interact_type = tool.get('interact_type', 'none')

    if tool_type in ("tile", "tree"):
        return create_tile_object(tool, world_pos, interact_type)

    elif tool_type == "collision_box":
        return create_collision_box_object(tool, world_pos, interact_type)

    elif tool_type == "character":
        return create_character_object(world_pos)

    elif tool_type == "portal":
        return create_portal_object(world_pos)

    elif tool_type == "sunshiyi":
        return create_sunshiyi_object(tool, world_pos)
    else:
        return None