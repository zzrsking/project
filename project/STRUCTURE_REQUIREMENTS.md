# STRUCTURE 要求清单

定义一个新的 Structure 子类（如横墙、House）时，**必须满足的条件**。

只讲三个交互面：**与 port 的联动**、**如何确定渲染顺序**、**怎样给 renderer 淡入淡出的信号**。
这三块要么满足、要么出问题 —— 而且问题通常出现在**画面上**，不会报错。

---

## 一、与 port 的联动

### 1.1 必须提供的成员

| 成员 | 谁在什么时候调 | 必须做什么 |
|---|---|---|
| `get_dock_geometry()` | 取/建端口、算吸附偏移 | 返回 `RectDock(grid_w, grid_h, cell_w, cell_h, cx, cy)`。**返回 None = 这个结构不能连** |
| `port_node_for_cell(cell)` | 懒生成该格端口节点 | 返回 Port 子类，或 None（该格没有口） |
| `port_name_for(side, index, cell)` | 建 `ports` 字典 | 返回语义名（如 `"top_left"`），或 None（不收录） |
| `_sprite_at_port(port)` | 连接后换图 + 画端口标记 | 返回该格精灵；**返回 None 会让端口标记静默画不出来** |
| `on_port_changed(a, b, sa, sb, *, connected)` | 一对端口刚连上 / 刚断开，两端各调一次 | 默认实现：`connected=True` 时取 `a` 所在格精灵 → 调它的 `on_dock(...)` + `_decorate_leaf`；`connected=False` 时**什么都不做**（重算交给 settled） |
| `on_topology_settled()` | 一次连接/断开**事务结束**，每结构最多一次 | **自决策入口**：读端口簇，刷新自身所有格贴图。默认空实现 |
| `move_by(dx, dy)` | 本结构作为主动方移动 | 整体平移（含所有叶子） |

> 组合结构（House / Hall）额外须知：它们没有单一网格，端口要**聚合子部件**。
> 见 `House.ports` / `Hall.ports` —— 两个都提供 `ports` 字典 + `port(name)`，
> 口径一致（子部件具名端口带 `<部件>:` 前缀，另加端别别名）。

### 1.2 约束

1. **dock 几何必须与叶子实际布局一致**：`grid_w × cell_w ≈ 叶子包围盒宽`。
   不一致 → `dock_offset_px` 算出的吸附偏移是错的，墙会贴歪。
2. **端口挂在"边界格"上，不是挂在"边"上**。同一个角格可能同时属于 `top` 和 `left` 两条边；
   `get_side_ports()` 在两条边上返回的是**同一个节点实例**，方向由 `connect_to(self_side=...)` + 节点自身的 `connectable_sides` 决定。
3. **换贴图只有一条路径：结构算完 → 精灵照贴。**
   结构侧用**一个函数**算出整墙要贴什么（例如 `VerticalWall.wall_texture_plan()`
   返回每格 `(cell_type, (帧号, 翻转))`），再让每个精灵 `apply_frame(帧号, 翻转)`。
   **不要在 Structure 里直接改 `sprite.image`**，也不要让精灵自己去读结构的状态重算判定
   —— 判定在两处各写一遍，迟早会不一致（`wall_texture_plan` 那个 `_tail_crossed` 笔误
   就是这么被掩盖了很久的）。
   只有"逐对、只影响一格"的少数结构（如地板）才走 `on_port_changed` → `sprite.on_dock(...)`。
4. **一个端口能连几个由 `Port.max_connections` 决定**：缺省 = `len(connectable_sides)`。
   墙族四角/竖墙口显式写 `max_connections = 1`（连上即"已满"→红）；
   地板角口写 `2`（一个角可挂两个连接）。满了会被 `PortBusyError` 拒绝。
5. **缩放因子（cell 尺寸）不同的两端不能直连**：`connect_to` 会拒，并触发 `notify_adapter_needed`。
6. **同一类型对的规则只能注册一次**（`port.RuleRegistry` 保护）。重复组装房子/场景时必须用幂等注册
   （见 `wall_rules.install`）。

### 1.3 语义名约定（`ports` 字典的 key）

| 结构 | 名字 |
|---|---|
| 横墙 | `top_left` / `top_right` / `bottom_left` / `bottom_right` |
| 竖墙 | `top`（头） / `tail`（尾段） / `bottom`（底） |
| 地板 | `top_left` / `top_right` / `bottom_left` / `bottom_right`（下两角 `max=2`） |
| House | 聚合子结构端口 —— 见 `House.ports` / `House.port()`（带 `<部件>:` 前缀 + 端别别名） |

竖墙另有一条：它的**头/尾格在不同连接方向上属于墙族不同的角词汇**，所以 `VerticalWallPort.edge_type(side)` 会把 side 归一化后再查规则。

---

## 二、如何确定渲染顺序

### 2.1 必须提供的成员

| 成员 | 作用 |
|---|---|
| `_iter_sprites_with_depth()` | 产出 `(叶子, 深度键)`。**结构内部**的排序靠它 |
| `leaf_base_y(leaf)` | 这片叶子**站在哪条地面线**上（默认：整块共用一条） |
| `leaf_depth(leaf)` | `leaf_base_y(leaf) + layer_offset`。**遮挡结构逐片参与全局排序**时用这个 |
| `depth_base_y()` | 结构**作为整体**参与全局排序时的地面线 |
| `layer_offset` | 同层内的深度微调常数 |
| `is_occluder` | `True` = 叶子**逐片**作为独立绘制单位（别的对象能插进叶子之间）；`False` = 整块作为原子 |
| `get_sprite_order()` | 按内部深度排好并**缓存**的叶子列表；几何变更后置空 |

### 2.2 约束（2.5D 正确性的来源）

1. **墙 = 立面 → 整面墙共用一条地面线。**
   逐行各用自己那一行的下缘，会让高墙的上半截排到屋内角色身后 —— 出现"半堵墙在人身前、半堵墙在身后"的破图。
2. **水平地面（地板）→ 整块取最北边 − 1。**
   地面必须画在一切"站在它上面"的东西之前，否则角色会被自己脚下的地砖盖住。
3. **竖墙 = 一个整体，墙脚线取【视觉最下缘】。**
   不是"沿地面铺开那一段的南缘" —— 墙画到哪里，玩家就认为它站到哪里。
   （`VerticalWall.DEPTH_BASE_ROW`，当前 `"bbox"`。）
4. **每片叶子必须有有效的 `world_position`（尺寸 > 0）。**
   深度、遮挡相交、空间网格、block 激活**全都依赖它**；尺寸为 0 会让这几项一起静默失效。
5. **`is_occluder=True` 的结构，叶子必须覆盖全部可绘制内容，且 `_iter_sprites_with_depth` 一片不漏。**
   渲染器只会画 `iter_sorted_leaf_sprites()` 返回的东西。
6. **几何变更后必须 `_invalidate_order()`**（`move_by` / 换图 / `update` 里）。
   墙的顺序是缓存的，不清缓存就会用到旧位置。

### 2.3 全局排序怎么用它

```
depth_sorted_draw_units(objs)
  ├ 遮挡结构(is_occluder)   -> 逐片，键 = leaf_depth(leaf)
  ├ 非遮挡结构 / 普通对象    -> 整块，键 = depth_y(obj)（默认取包围盒下缘）
  └ 稳定排序（同键保持原顺序）
```

---

## 三、怎样给 renderer 淡入淡出的信号

### 3.1 必须提供的成员

| 成员 | 作用 |
|---|---|
| `occlusion_policy` | `FADE`（默认）/ `NONE`（永不淡出）/ `CUTAWAY` / `OUTLINE` |
| `occlusion_policy_for(leaf)` | 逐叶策略；默认返回结构级 |
| `occlusion_owner_of(leaf)` | **淡出粒度**：这片叶子以谁为单位淡。默认 `self` |
| `is_occluder` | 决定 renderer 拿到的是"整块"还是"逐片" —— **只有逐片才可能只淡挡住的那一面墙** |
| 叶子的 `image` + `world_position` | renderer 据此算 `art_rect_of`（**实际绘制范围**）来判相交 |

### 3.2 约束

1. **一面墙必须共用一个 owner。**
   组合结构必须覆写 `occlusion_owner_of`，返回**叶子所属的部件** ——
   见 `House`：返回 `bottom_wall`/`left_wall`/…，所以四面墙各自独立淡。
   若默认返回整栋房子，玩家被前墙挡住时后墙、侧墙、地板会**一起褪色**，整栋像掉色。
2. **不要在结构里改 `sprite.image` 来做淡出。** 淡出由渲染器按 alpha 应用，结构侧只出信号。
3. **判定是通用规则，不许写结构特例**：一个单位算遮挡者要**同时**满足
   ① 绘制顺序排在焦点之后（更近） ② 与焦点的**实际绘制范围**像素相交。
   只更近不相交，对画面毫无影响，不能算遮挡。
4. **默认 `FADE` 不会在没遮挡时改变画面** —— 判定只在真的挡住时触发。
   要永不淡出的结构显式写 `NONE`。

### 3.3 renderer 侧怎么消费（结构不用管，但要知道契约）

```
renderer._occlusion_pass(dyn, dt)
  ├ focus = world.active_player
  ├ occlusion_frame(objs, focus)
  │    ├ depth_sorted_draw_units()   绘制顺序
  │    ├ owner_index_of()            叶子 -> occlusion_owner_of(leaf)
  │    └ occluders_of()              判定 + 取 occlusion_policy_for
  └ OcclusionFader.update(occ, dt)   缓动 alpha（进入从 1.0 起淡、离开回升、恢复后删除）
```

---

## 四、定义新 Structure 的检查清单

```
□ get_dock_geometry() 与叶子布局一致（要能连的话）
□ port_node_for_cell / port_name_for 声明了边界格上的口
□ _sprite_at_port() 能返回该格精灵（否则端口标记画不出来）
□ 换贴图交给 `sprite.apply_frame(帧号, 翻转)`，判定只在结构侧算一次，不直接改 sprite.image
□ _iter_sprites_with_depth() 一片不漏，深度键是有限数
□ 墙：整面共用一条地面线；地面：整块取最北边；竖墙：墙脚取视觉最下缘
□ 每片叶子 world_position 尺寸 > 0
□ 几何变更后 _invalidate_order()
□ is_occluder=True 时：重写 occlusion_owner_of 按部件分（否则整块一起淡）
□ 不想淡出的结构显式写 occlusion_policy = NONE
□ 在 structure_persist.register_structure_kind() 里登记存档配方
  （不登记的话，这个结构一进存档就会明确报错；见 STRUCTURE_PERSISTENCE.md）
```

> 存档说明：结构**不进对象图**，只存"创建配方 + 用 instance_id/端口语义名描述的连接表"。
> 所以新结构必须保证「同样的参数 + 同样的仓库规则 ⇒ 同样的几何」，否则读档会画歪；
> 同时 `port_name_for` 给出的语义名要稳定 —— 连接表就是靠它找端口的。

---

## 五、现有实现可作范例

| 结构 | 适合参考哪一条 |
|---|---|
| `HorizontalWall` | 四角端口、整面共用地面线、`_map` 走 `WALL_SPEC` |
| `VerticalWall` | 头/尾/底三段端口、侧边可连、**墙脚取视觉最下缘**、行段声明、`wall_texture_plan()` → `apply_frame`（本文件 §1.2-3 的范例） |
| `Floor` | 水平地面（整块垫底）、四角下角 `max=2` |
| `House` | `is_occluder` 逐片展开、**`occlusion_owner_of` 按部件分**、`ports` 聚合子部件端口 |
| `Hall` | 组合结构 + 开口端面端口；`ports` / `port()` 与 `House` 同口径 |
