# 场景存档（一键存 / 读整个世界）

> 一句话：**能被地图文件重建的东西不存**。地形 11400 块地块本来就不该进存档。
>
> 实测（test 地图 + demo 场景）：**12.7 MB / 存档 6.7 s → 13.9 KB / 0.36 s**，
> 连续读档 3 次对象数、贴图指纹完全不变。自检：`python tests/test_scene_save_load.py`。

代码：`src/core/scene_io.py`。游戏内按键已经走这条路（`ui_manager.save_func/load_func`）。

---

## 一、用法

```python
from src.core.scene_io import save_scene, load_scene

stats = save_scene(world, "save_test.json")   # 默认跳过"地图铺出来的层"
stats = load_scene(world, "save_test.json")   # 重建地形 → 放回对象 → 重连结构
```

读档返回统计信息（对象数、失败数、是否找回玩家/相机、用的哪张地图），
存档打印一行摘要：`对象 11，跳过地形 11413，13.9 KB`。

旧接口仍然可用（`serializer.save/execute/load` 的对象列表格式），
`load_scene` 也能直接读旧文件，便于平滑过渡。

## 二、格式

```json
{
  "format": "dsh_scene/1",
  "map": "terrain\\map.tmx",
  "layers": [ {"name": "Groud", "kind": "terrain", "order": 1, "source": "tmx:Groud"}, … ],
  "objects": [
    {"label": {"type": "world_obj", "layer": "test"}, "data": { …结构配方/精灵属性… }},
    {"label": {"type": "active_player", "layer": "characters"}, "data": { … }}
  ],
  "camera": { … },
  "stats": {"skipped_terrain": 11413, "saved_objects": 11}
}
```

只有 `objects` 是"必须存"的部分：

| 内容 | 怎么存 | 说明 |
| --- | --- | --- |
| 结构（墙/房/走廊/桌） | 创建配方 + 连接表 | `STRUCTURE_PERSISTENCE.md` |
| 普通精灵（玩家/家具/装饰） | 属性序列化 | 图片走"路径 / 配方" |
| 图片 | 路径 / 派生配方 / 地图瓦片 | `SERIALIZER_IMAGE_FORMAT.md` |
| **地形（上万个地块）** | **不存** | 读档时由 `map.tmx` 重新铺 |

`layers` 仍然全部记下来（名字/顺序/kind/可见性/来源），读档时先把层建好再铺地图。

## 三、"跳过"的判定

一块层会被跳过，只要它**来自地图文件**：

- `LayerSpec.source` 以 `"tmx:"` 开头（`LayerSpec.from_tmx` 的写法），或
- 层名出现在 `map.tmx` 的层列表里。

**不看 `kind`**：`kind=TERRAIN` 的层也可能是编辑器里手工铺的（名字里带 `ground`
就会被判成 TERRAIN），那种层必须照存 —— 否则读档后手工内容会丢。
想连地图层一起存（比如你要在地图上手工改地块），用 `save_scene(world, path, skip_terrain=False)`。

## 四、读档顺序（别调换）

```
1) world.clear_all()
2) 按存档恢复层规格（缺的才建）
3) 按 map 重建地形：setup.load_tmx_data + load_tmx_to_world
4) 逐个反序列化对象并加回世界（**每个对象单独 try/except**）
5) 恢复相机 + 接上当前玩家
6) serializer.run_post_load_hooks()：结构之间的连接在这里统一重放
```

第 4 步的"逐个保护"是必需的：旧实现（`ui_manager.load_func`）在循环里直接
`add_game_object`，任何一个对象抛异常都会**中断整次读档** ——
表现就是"只有前半截对象回来了"（例如只回来了桌子，后面的墙/房子/走廊全没）。

## 五、这条路依赖的两个引擎修复

| 问题 | 现象 | 修法 |
| --- | --- | --- |
| `world.add_game_object` 直接读 `obj.is_collidable` | `TablePlantTile` 这类装饰精灵没有该属性 → 抛异常 → 读档循环中断 | 改用 `getattr(obj, "is_collidable", False)`，并给 `Genetic` 补类属性 `is_collidable = False` |
| 地图里有 11 个**无名** `TiledObjectGroup` | `add_game_object(obj, None)` 被兜底成 `"default"`：地图内容混进引擎 own 的 default 层，且这层"看起来不是地图来的" → 存档把它也存下来 → 读档后地形铺一遍、对象又加一遍 = **重复** | `setup.load_tmx_to_world` 给无名层稳定命名（`tmx_layer_N`）并按 TMX 建好层 spec（`source="tmx:…"`） |

## 六、性能红线（别退回去）

`tests/test_scene_save_load.py` 钉住了这些：

- 存档 < 200 KB；存档/读档各 < 3 s
- 连续 3 次读档：结构数量与类型一致、各层对象数不漂移、总对象数守恒
- 连续 3 次读档：地形 + 结构叶子贴图指纹与启动时**完全一致**
- 读档后再存档，体积不膨胀

另有一条隐藏成本：给"来源未知"的贴图做内容匹配是**逐对象**的。
地形上万个地块（只有 66 张不同贴图）如果没登记 `map_tile` 来源，
就会每个对象都做一次整图 md5 —— 实测 6.7 s 全耗在这里。
所以 setup 一定要登记瓦片来源，别把 `register_map_tile_source` 那两行删掉。
