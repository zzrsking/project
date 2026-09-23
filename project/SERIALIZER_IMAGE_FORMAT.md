# 存档里的图片怎么存（序列化格式说明）

## 一句话

**图片不写像素，只写"它是怎么被创建出来的"。**

能否只写路径，取决于 `src/core/resource_manager.py` 里的**来源登记表**：
任何由 `load_image / load_tileset / load_procedural_surface` 产生的 Surface，
都会把「路径 + 切分方式 + 帧号 + 变换」登记下来；`subsurface()` 裁出来的片子
则通过父链还原成「父图来源 + 裁剪矩形」。

## 五种记录（按优先级）

| resource_format | 何时出现 | 记录内容 |
| --- | --- | --- |
| `load` | 由文件加载出来的帧 | `path` / `split` / `index` / `scale` / `flip_x` / `flip_y` / `colorkey` |
| `procedural` | 程序化生成的 Surface | `resource_type` / `params` |
| `crop` | `subsurface()` 裁片 | `parent`（递归的来源记录）/ `rect` |
| `recipe` | 派生出来、内容与原图不同的图 | `builder` / `spec` / `parents[]` |
| `map_tile` | 地图（TMX）铺出来的地块 | `map` / `gid` |
| `pixels` | 兜底：来源实在未知 | `size` / `palette` / `rle`（紧凑编码） |

> 地形地块**一定**要登记 `map_tile` 来源（setup 里已做）。没登记的话，一张 64×64
> 地块就是约 1 KB 的压缩像素，而且每个对象都要做一次整图 md5 做内容匹配 ——
> 实测 test 地图 11400 块地块：**12.7 MB / 序列化 6.7 s**；登记后是 **0.1 s 级**。

**默认值一律不写**。一张"单帧、无缩放、无翻转"的图片，记录就只剩两行：

```json
{"resource_format": "load", "path": "terrain/graphics/environment/House.png"}
```

带帧号/缩放/翻转时才会多出字段：

```json
{"resource_format": "load", "path": "characters/default_player.png",
 "split": {"mode": "horizontal", "count": 4}, "index": 2, "scale": [64, 64]}
```

`split.mode` 支持 `horizontal`(count) / `vertical`(count) / `grid`(cols,rows)
—— 也就是 `split_horizontal / split_vertical / split_grid` 三种内置切分方式。
`split_custom` 的回调无法序列化，这类帧不会被冒充成路径，直接走像素兜底。

裁片：

```json
{"resource_format": "crop",
 "parent": {"resource_format": "load", "path": "characters/default_player.png",
            "split": {"mode": "horizontal", "count": 4}, "index": 1, "scale": [64, 64]},
 "rect": [8, 8, 16, 16]}
```

派生配方（`recipe`）：**算出来、而不是加载出来**的图 —— 内容与原图不同，
像素哈希也匹配不上，所以必须记"怎么算的"：

```json
{"resource_format": "recipe", "builder": "flip",
 "spec": {"x": true, "y": false},
 "parents": [{"resource_format": "load", "path": "terrain/.../House_33",
              "split": {"mode": "grid", "cols": 8, "rows": 8}, "index": 33}]}
```

内置 builder：

| builder | spec | parents | 用途 |
| --- | --- | --- | --- |
| `flip` | `{x, y}` | `[母图]` | 竖墙默认纹理（33 号要水平翻转当左墙用） |
| `scale` | `{size: [w, h]}` | `[母图]` | 桌上盆栽（64→40）等 |
| `rotate` | `{angle}` | `[母图]` | `rotate_surface()` |
| `compose` | `{layers: [{crop, scale, pos}, ...]}` | `[母图, 层1源图, 层2源图, ...]` | 竖墙侧脸（base 副本 + 裁剪缩放后的侧脸片 blit） |

**优先用 resource_manager 的封装**，它们会顺手登记配方：

```python
from src.core.resource_manager import flip_surface, scale_surface, rotate_surface, compose_surfaces

art = flip_surface(frame, True, False)                   # 等价于 pygame.transform.flip
art = scale_surface(frame, (40, 40))                     # 等价于 pygame.transform.scale
art = compose_surfaces(base, [{'src': side, 'crop': (0, 0, 64, 64),
                               'scale': (32, 64), 'pos': (0, 0)}])   # 等价于 copy()+blit
```

自己写新变换时，用 `register_surface_builder(name, fn)` 注册一个
`fn(spec, parents) -> Surface`，即可让这类图也走"只存配方"。
**父图来源未知时不会登记配方**，此时照旧退回像素保存 —— 宁可贵，不许读档丢内容。

## 地图瓦片（`map_tile`）：地形只写"第几号"

地形是"地图文件 + gid"生成的，所以它的创建路径就是这两个值：

```json
{"resource_format": "map_tile", "map": "terrain/map.tmx", "gid": 12}
```

登记方式（`setup.load_tmx_data` / `load_tmx_to_world` 里已经做了）：

```python
from src.core import resource_manager

resource_manager.register_map_tile_loader(lambda map_path, gid: …)   # 由 setup 注入
resource_manager.register_map_tile_source(surf, "terrain/map.tmx", gid)
```

- 加载器由 `setup` 注入，返回的正是 **pytmx 缓存的那个 Surface 对象** ——
  所以读档后地形用的是同一批贴图，像素逐位一致，也不额外占内存。
- 已有更精确来源（例如 `load_image` 出来的帧）时，**不会被地图来源顶掉**。
- 没注入加载器 / 查不到 gid 时，`map_tile` 视为"不可重建"，序列化器不会写它。

## 性能守则（别再退回去）

"来源未知"的贴图要做内容匹配，这是**逐对象**成本。两道闸：

1. **负缓存**：同一个 Surface 对象探过一次没命中，就不再重复探测
   （地形上万个精灵其实共用几十张贴图）。
2. **采样预筛**：先比 `(宽, 高, 采样哈希)` 找候选，没有候选立刻返回；
   只有候选存在时才做整图 md5 复核（真命中不会被漏，假阳性不会算错）。

## 像素兜底也尽量少写

被迫写像素时（手绘、拼装、来源未知的图）有两条救济：

1. **内容哈希回退**：先把像素做一次 MD5，去"已登记的已知资源"里找内容完全相同的图。
   命中就改存那条路径 —— `.copy()`、重新拼出来但内容没变的图，都能被救回路径。
2. **紧凑编码**：调色板拼成一个字符串（每 8 个十六进制字符一个 RGBA 颜色），
   RLE 拼成 `"count,idx,count,idx,..."`，比嵌套数组小很多。
   两种老写法（字符串列表 + 嵌套数组）仍能读取。

## 怎么让新代码也享受"只存路径"

如果某处 Surface 是手工造的，但你知道它其实来自某个文件，显式登记即可：

```python
from src.core.resource_manager import register_surface_source, make_load_source_info

register_surface_source(surf, make_load_source_info("terrain/tree.png", index=3, frame_count=4))
```

## 兼容性

- 旧存档（`relative_path` + `transform{scale,frame_count,flip_*}`、老像素数组写法）**仍可读**，
  由 `_expand_source_record` / `_deserialize_pixels_surface` 里的兼容分支处理。
- `get_surface_source()` 保留旧签名（返回 `('load', (path, transform))`），
  新代码请用 `get_surface_source_info()`。
- `unload_all()` 会连同来源登记表一起清空；之后重新加载同一张图会**重新登记**来源
  （不会像以前那样因为物理缓存命中而丢掉元数据）。

## 自检

```
python tests/test_serializer_image_source.py     # 图片：路径 / 裁片 / 派生配方 / 地图瓦片 / 像素兜底 / 旧存档兼容
python tests/test_structure_serialize.py         # 结构：配方存档 / 连接恢复 / 环 / 贴图逐片一致
python tests/test_scene_save_load.py             # 场景：一键存读 / 体积 / 多次读档不崩坏
```

图片测试固化了几条硬性关系：路径图必须不含 palette、`.copy()` 必须被救回路径、
被改过的图必须无损地走像素、派生图必须走配方、地图瓦片只写 map+gid、
旧存档必须还能读、反序列化后必须仍是缓存里的同一批对象。

相关文档：结构存档见 `STRUCTURE_PERSISTENCE.md`，整个场景的存读见 `SCENE_SAVE_LOAD.md`。
