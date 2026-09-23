"""scene_io.py —— 场景级存档：一次调用保存 / 读取整个场景

## 为什么需要它

逐对象序列化（serializer.save 每个精灵一次）会把**能由地图文件重建的东西也存下来**：
`terrain/map.tmx` 光是地块就有 11400 个（地面 10000 + 水 357 + 坡 597 + 房地板 234 + …），
它们占了存档 99% 的体积和读档时间，而它们本来就是 setup.py 从地图文件铺出来的、
读档时重新铺一遍即可 —— 存下来纯属浪费，还容易因为"逐个加入世界"中途抛异常而读档失败。

## 格式

```json
{
  "format": "dsh_scene/1",
  "map": "terrain/map.tmx",                 // 地形由它重建，不再逐个保存
  "layers": [{"name": "Groud", "kind": "terrain", "order": 3, ...}, ...],
  "objects": [{"label": {"type": "world_obj", "layer": "test"}, "data": {...}}, ...],
  "camera": {...},
  "stats": {"skipped_terrain": 11400, "saved_objects": 25}
}
```

只有 `objects` 是"必须存"的东西：结构走配方（`STRUCTURE_PERSISTENCE.md`），
普通精灵走属性序列化，图片一律走"路径 / 配方 / 地图瓦片"（`SERIALIZER_IMAGE_FORMAT.md`）。

## 用法

```python
from src.core.scene_io import save_scene, load_scene

save_scene(world, "save_test.json")     # 一次调用，默认跳过地形层
load_scene(world, "save_test.json")     # 重建地形 + 放回对象 + 重连结构
```

`load_scene` 也能读**旧格式**（没有 `format` 字段的对象列表），便于平滑过渡。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from src.utils.layer_v2 import LayerKind, LayerSpec, dataclasses_replace

import serializer

SCENE_FORMAT = "dsh_scene/1"
DEFAULT_MAP_PATH = "terrain\\map.tmx"
DEFAULT_SCENE_FILE = "save_test.json"

_LAYER_FIELDS = ("name", "order", "floor", "kind", "visible", "opacity",
                 "y_sorted", "physics", "synthetic", "source")


# ------------------------------------------------------------------ #
# 图层规格 <-> JSON
# ------------------------------------------------------------------ #
def layer_spec_to_dict(spec: LayerSpec) -> dict:
    data = {}
    for field in _LAYER_FIELDS:
        value = getattr(spec, field, None)
        data[field] = value.value if isinstance(value, LayerKind) else value
    return data


def layer_spec_from_dict(data: dict) -> Optional[LayerSpec]:
    if not data or not data.get("name"):
        return None
    kwargs = {}
    for field in _LAYER_FIELDS:
        if field == "name" or field not in data:
            continue
        kwargs[field] = data[field]
    if "kind" in kwargs:
        kwargs["kind"] = LayerKind(str(kwargs["kind"]))
    return LayerSpec(name=str(data["name"]), **kwargs)


# ------------------------------------------------------------------ #
# 保存
# ------------------------------------------------------------------ #
def build_scene(world, map_path: str = DEFAULT_MAP_PATH,
                skip_terrain: bool = True) -> Dict[str, Any]:
    """把世界收成一份场景数据（不落盘，便于检查/统计）"""
    layers = []
    objects = []
    skipped = 0
    map_layers = _map_layer_names(map_path) if skip_terrain else set()

    for bucket in world.layers:
        spec = bucket.spec
        layers.append(layer_spec_to_dict(spec))

        if skip_terrain and _is_rebuildable_terrain(spec, map_layers):
            skipped += len(bucket.sprites)          # 地形：读档时由地图文件重建
            continue

        for obj in bucket.sprites:
            label = {
                "type": "active_player" if obj is getattr(world, "active_player", None)
                        else "world_obj",
                "layer": spec.name,
            }
            try:
                data = serializer.serialize(obj)
            except Exception as e:
                print(f"[scene_io] 对象 {type(obj).__name__} 序列化失败，已跳过: {e}")
                continue
            objects.append({"label": label, "data": data})

    scene = {
        "format": SCENE_FORMAT,
        "map": map_path,
        "layers": layers,
        "objects": objects,
        "camera": _safe_serialize(getattr(world, "camera", None)),
        "stats": {"skipped_terrain": skipped, "saved_objects": len(objects)},
    }
    return scene


def save_scene(world, filename: str = DEFAULT_SCENE_FILE,
               map_path: str = DEFAULT_MAP_PATH,
               skip_terrain: bool = True) -> Dict[str, Any]:
    """保存整个场景：返回统计信息（保存了多少对象、跳过了多少地形）"""
    scene = build_scene(world, map_path=map_path, skip_terrain=skip_terrain)
    with open(filename, "w", encoding="utf-8") as f:
        # 紧凑写：场景文件少一层缩进能省掉近一半体积
        json.dump(scene, f, ensure_ascii=False, separators=(",", ":"))
    scene["stats"]["bytes"] = os.path.getsize(filename)
    print(f"[scene_io] 已保存场景 {filename}：对象 {scene['stats']['saved_objects']}，"
          f"跳过地形 {scene['stats']['skipped_terrain']}，"
          f"{scene['stats']['bytes'] / 1024:.1f} KB")
    return scene


def _map_layer_names(map_path) -> set:
    """地图文件里有哪些层（这些层的对象读档时会被重建，不该再存一份）"""
    if not map_path:
        return set()
    try:
        import setup
        tmx = setup.load_tmx_data(map_path)
        return {spec.name for spec in setup.get_tmx_layers(tmx) if spec.name}
    except Exception as e:
        print(f"[scene_io] 读取地图层列表失败（将只按来源判断）: {e}")
        return set()


def _is_rebuildable_terrain(spec: LayerSpec, map_layers: Optional[set] = None) -> bool:
    """这块层是不是"由地图文件铺出来的"（读档时 setup.load_tmx_to_world 会原地重建）

    判据：① 层名出现在地图文件的层列表里；② 或者 source 是 "tmx:层名"
    （LayerSpec.from_tmx 的写法）。**不看 kind**：kind=TERRAIN 的层也可能是
    编辑器里手工铺的（名字带 ground 就会被判成 TERRAIN），那种层必须照存。
    """
    if (spec.source or "").startswith("tmx:"):
        return True
    if map_layers and spec.name in map_layers:
        return True
    return False


def _safe_serialize(obj):
    if obj is None:
        return None
    try:
        return serializer.serialize(obj)
    except Exception as e:
        print(f"[scene_io] {type(obj).__name__} 序列化失败（相机等将在读档后保持默认）: {e}")
        return None


# ------------------------------------------------------------------ #
# 读取
# ------------------------------------------------------------------ #
def load_scene(world, filename: str = DEFAULT_SCENE_FILE) -> Dict[str, Any]:
    """读取场景：重建地形 → 放回对象 → 重连结构 → 接上相机/玩家。返回统计信息。"""
    with open(filename, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        # 旧格式（对象列表）：地形也一起存在里面，按老路子加回去
        return _load_legacy_object_list(world, raw)
    if not isinstance(raw, dict) or raw.get("format") != SCENE_FORMAT:
        raise ValueError(f"{filename} 不是场景文件（format={raw.get('format') if isinstance(raw, dict) else type(raw)}）")

    world.clear_all()
    _apply_layer_specs(world, raw.get("layers") or [])
    _rebuild_map(world, raw.get("map"))

    objects, labels = _add_objects(world, raw.get("objects") or [])
    camera = _restore_camera(world, raw.get("camera"))
    player = _restore_player(world, objects, labels, camera)

    # 跨对象收尾（结构之间的连接就靠这一步）
    try:
        serializer.run_post_load_hooks(objects, labels)
    except Exception as e:
        print(f"[scene_io] 读档后置处理失败: {e}")

    stats = {
        "objects": len(objects),
        "failed": len(raw.get("objects") or []) - len(objects),
        "player": player is not None,
        "camera": camera is not None,
        "map": raw.get("map"),
    }
    print(f"[scene_io] 已读取场景 {filename}：对象 {stats['objects']}"
          f"（失败 {stats['failed']}），地图 {stats['map']}")
    return stats


def _apply_layer_specs(world, layer_dicts) -> None:
    for data in layer_dicts or []:
        spec = layer_spec_from_dict(data)
        if spec is None:
            continue
        if spec.name in world.layers:
            continue
        try:
            world.layers.add_layer(spec)
        except Exception as e:
            print(f"[scene_io] 图层 {spec.name} 恢复失败: {e}")


def _rebuild_map(world, map_path) -> None:
    """按地图文件重建地形（这一步替代了存档里的上万个地块对象）"""
    if not map_path:
        return
    try:
        import setup
        tmx = setup.load_tmx_data(map_path)
        for spec in setup.get_tmx_layers(tmx):
            if spec.name not in world.layers:
                world.layers.add_layer(spec)
        setup.load_tmx_to_world(tmx, world)
    except Exception as e:
        print(f"[scene_io] 地形重建失败（map={map_path}）: {e}")


def _add_objects(world, entries) -> tuple:
    objects, labels = [], []
    for entry in entries or []:
        label = entry.get("label") or {}
        try:
            obj = serializer.deserialize(entry.get("data"))
        except Exception as e:
            print(f"[scene_io] 对象反序列化失败（{label.get('layer')}/{label.get('type')}）: {e}")
            continue
        if obj is None:
            continue
        objects.append(obj)
        labels.append(label)
        if label.get("type") == "camera":
            continue
        try:
            world.add_game_object(obj, label.get("layer"))
        except Exception as e:
            # 单个对象加不进世界，不该毁掉整次读档
            print(f"[scene_io] 加入世界失败 {type(obj).__name__}: {e}")
    return objects, labels


def _restore_camera(world, camera_data):
    if not camera_data:
        return None
    try:
        camera = serializer.deserialize(camera_data)
    except Exception as e:
        print(f"[scene_io] 相机反序列化失败: {e}")
        return None
    if camera is not None:
        world.camera = camera
    return camera


def _restore_player(world, objects, labels, camera):
    player = None
    for obj, label in zip(objects, labels):
        if label.get("type") == "active_player":
            player = obj
            break
    if player is None:
        return None
    world.active_player = player
    if camera is not None:
        try:
            camera.bind_object = player
        except Exception:
            pass
    return player


def _load_legacy_object_list(world, raw: list) -> Dict[str, Any]:
    """旧格式（对象列表）读档：地形也在列表里，逐个加回世界"""
    world.clear_all()
    objects, labels = _add_objects(world, raw)
    camera = None
    for obj, label in zip(objects, labels):
        if label.get("type") == "camera":
            camera = obj
            break
    if camera is not None:
        world.camera = camera
    _restore_player(world, objects, labels, camera)
    try:
        serializer.run_post_load_hooks(objects, labels)
    except Exception as e:
        print(f"[scene_io] 读档后置处理失败: {e}")
    return {"objects": len(objects), "failed": len(raw) - len(objects), "legacy": True}
