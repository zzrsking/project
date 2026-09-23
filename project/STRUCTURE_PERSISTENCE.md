# Structure 怎么存档 / 读档

> 结论先说：**结构永远不进对象图**。存档里只写"它是怎么被创建出来的"，
> 以及一张**用 ID 和语义名描述的连接表** —— 所以 `Structure ↔ Port ↔ Structure` 的环
> 从根上不存在，不存在"序列化到一半递归爆栈"这种事。

代码：`src/core/build_game/structure_persist.py`（在 `serializer` 导入时自动接入）。
自检：`python tests/test_structure_serialize.py`。

---

## 一、为什么不能按对象图存

| 环 / 障碍 | 说明 |
| --- | --- |
| `Structure.connections` → `Structure` | 两堵墙互相连接就是双向引用 |
| `Structure._parts` → `Structure` | `House` / `Hall` 聚合子结构 |
| `Port.structure` → `Structure`，`Port.edges[side]` → `Port` | 端口与宿主、端口与端口 |
| `pygame.sprite.Group` | 已被序列化器拉黑；读档后组必然是空的 |
| 格子贴图 | 是加载 / 翻转 / 合成出来的 Surface —— 逐属性存会把整张图写成像素 |

逐属性序列化即使没被环卡死，也只能还原出一个"没有端口、没有精灵组"的空壳。

## 二、配方长什么样

```json
{
  "kind": "hwall",
  "cls": "src.core.build_game.horizontalwall.HorizontalWoodWall",
  "size": [6, 3],
  "at": [900.0, 1250.0],
  "cell": [64.0, 64.0],
  "overlap_bottom": true,
  "layer_offset": 1,
  "occlusion": "fade",
  "side_face": {"kind": "id", "value": "49"},
  "id": "e0409d1a",
  "part_ids": {"floor": "3f21...", "left_wall": "9ab0..."},
  "links": [
    {"part": null, "port": "top", "side": "left",
     "peer_id": "77c1...", "peer_port": "top_right", "peer_side": "right"}
  ]
}
```

- `kind` 决定用哪套 `from_recipe`：`hwall` / `vwall` / `floor` / `house` / `hall` / `table`。
- `cls` 是具体类（`Hall` 走的是 `Hall.build`，`House` 走 `House.build`）。
- `id` 就是 `Structure.instance_id`：**这是连接表唯一的寻址方式**，读档后按它还原，
  于是部件之间的连接也能被正确指回来（`part_ids`）。
- `side_face` 既支持 House 编号字符串，也支持 Surface —— 后者复用图片那套
  「来源记录」（见 `SERIALIZER_IMAGE_FORMAT.md`），不会把侧脸图写成像素。
- **派生状态不存**：`orientation`、`_head_connected`、`_tail_cross` 等都由连接拓扑算得，
  写了反而可能与真实拓扑打架。它们由重连后的 `on_topology_settled()` 自己算回来。

## 三、读档三步（顺序不能换）

```
1) 每条配方独立重建一个 Structure
     ├ 内部部件（House 的地板/四墙、Hall 的地板/两墙）由各自的 build 自己连好
     └ 重建完记下锚点 obj._persist_anchor = 实际包围盒中心
2) 所有结构都建好后，按 links 统一重连
     ├ 端口寻址：优先语义名（ports["top_right"]），名字失效时退回"某边的某格"
     ├ 同一条连接会被两端各记一次 -> 第二条因"该边已占用"自然跳过
     └ 解析不到的对端/端口只告警，不中断整次读档
3) 位置复位 + 拓扑重算
     ├ connect_to 会让主动方吸附平移（Port._snap_self_to_peer），
     │  所以统一按锚点移回去（移动可能级联，最多迭代几轮直到收敛）
     └ 只对**真正参与过连接**的结构调 on_topology_settled() 重算贴图
       （没连过的结构一旦被"重算"，反而会和存档前不一样）
```

后两步由序列化器的**读档后置钩子**自动执行：

```python
# serializer.load() 内部，所有条目都反序列化完成之后
run_post_load_hooks([obj for obj, _ in result], [label for _, label in result])
```

单独调 `serializer.deserialize(data)` 不会触发跨对象重连（那时没有"对端"可言）；
需要时手动调：

```python
from src.core.build_game import structure_persist as sp
obj = serializer.deserialize(data)
sp.replay_pending_connections([obj])      # 需要把结构列表传进来做 id 索引
```

## 四、新增一种结构要做什么

```python
from src.core.build_game import structure_persist as sp

sp.register_structure_kind(
    'mywall',                     # kind 名（写进存档）
    MyWall,                       # 基类：子类按 MRO 自动匹配
    lambda obj: {'size': [...], 'at': [...], 'cell': [...]},   # 写
    lambda recipe: MyWall(...),                                 # 读
)
```

没登记的 `Structure` 子类在存档时会**明确报错**并指出该补哪一条 —— 不会静默丢对象：

```
TypeError: 结构 mymodule.MyWall 没有登记存档配方（kind=None）。
           请在 structure_persist.register_structure_kind() 里补一条，否则它无法被存档。
```

## 五、范围与已知限制

- 支持：横墙 / 竖墙 / 地板 / 房子（House）/ 走廊（Hall）/ 木桌（含桌上的物件）。
  走廊的两端开口端口、房子的部件端口都能作为连接端点存取。
- 跨结构的连接只在**两端都在同一次读档里**时才能恢复；只存了一端时按告警跳过
  （单独读一堵墙本来也无处可连）。
- 结构之间的连接**不包含**运行时命令历史（`Command` 注册表等），只保留拓扑结果。
- 桌上物件的嵌套对象走普通序列化（自带 memo，仍不会成环）。
- `layer_offset` 不传给 `build()`（`House.build` 收、`Hall.build` 不收，传了 Hall 会直接
  重建失败），而是在重建后作为普通属性设置。

## 六、和场景存档的关系

整个场景的存读在 `src/core/scene_io.py`（见 `SCENE_SAVE_LOAD.md`）：它只存**不能由
地图文件重建的对象**（结构就在其中），读档最后一步统一调 `serializer.run_post_load_hooks()`
—— 结构之间的重连就发生在那里。

⚠️ 一个容易踩的坑：`serializer` 对 `structure_persist` 是**延迟安装**的（首次
`serialize/deserialize` 时 import）。改成延迟安装之前，它在导入期就去拉 `build_game`，
在"setup 先被导入"等启动顺序下会撞上循环导入（`DialogueNode from partially initialized
module`），结果是**结构静默地没法存档**。

自检：`python tests/test_structure_serialize.py`（含环、连接、逐片贴图一致、world 级链路）。
