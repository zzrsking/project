from src.game.entity import Tiled


# ======================================================================
# 编辑器不处理的对象类型
#
# Tiled = 地图(TMX)瓦片图层铺出来的【底图地形】。它是地图数据，不是"场景对象"：
#   · 进属性面板没有意义（改它的坐标/名字不会改地图数据，重载地图就没了）；
#   · 它铺满整张地图、还压在物体层下面，会让真正的对象选不中（拾取永远先命中它）。
# 所以编辑器（悬停拾取 / 属性面板 / 删除）一律跳过 Tiled。
#
# 注意区分：
#   · TMX 瓦片图层  -> Tiled            -> 编辑器不管
#   · TMX 物体图层  -> GameObject       -> 编辑器照常编辑
#   · 编辑器放置的"地面/树木" 工具 -> LoadedImageObject(GameObject 子类) -> 照常编辑
# ======================================================================
EDITOR_IGNORED_TYPES = (Tiled,)


def is_editor_object(obj) -> bool:
    """该对象是否归编辑器管（地形瓦片、没有 world_position 的对象一律不管）。"""
    return (not isinstance(obj, EDITOR_IGNORED_TYPES)
            and hasattr(obj, "world_position"))


editor_tools = [
    {"name": "地面", "type": "tile", "image": "terrain\\ground", "layer": "terrain", "interact_type": "item"},
    {"name": "树木", "type": "tree", "image": "terrain\\tree", "layer": "objects", "interact_type": "item"},
    {"name": "角色", "type": "character", "image": "characters\\default_player", "layer": "characters", "interact_type": "player"},
    {"name": "碰撞块", "type": "collision_box", "image": None, "layer": "collision", "interact_type": "npc"},
    {"name": "墙", "type": "collision", "image": "special_obj", "layer": "collision", "interact_type": "npc"},
    {"name": "多边形墙", "type": "wall", "subtype": "polygon", "layer": "collision", "interact_type": "wall"},
    {"name": "圆形墙", "type": "wall", "subtype": "circle", "layer": "collision", "interact_type": "wall"},
    {"name": "矩形墙", "type": "wall", "subtype": "rectangle", "layer": "collision", "interact_type": "wall"},
    {"name": "传送门", "type": "portal", "layer": "objects", "interact_type": "item"},
    {"name": "孙石毅", "type": "sunshiyi", "image": "characters\\孙石毅", "layer": "characters", "interact_type": "npc"},
    {"name": "删除", "type": "delete", "image": None, "layer": None, "interact_type": "npc"},
]
default_tool = {"name": "删除", "type": "delete", "image": None, "layer": "objects", "interact_type": "npc"}
settings_buttons = [
    {"text": "保存进度", "action": "save"},
    {"text": "加载进度", "action": "load"},
    {"text": "返回", "action": "back"},
]
