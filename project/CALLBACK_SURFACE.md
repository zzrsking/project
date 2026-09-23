# 端口回调 / 结构间传递 —— 收敛方案（S0：只出方案，不动代码）

> 目标读者：改 `src/core/build_game/` 的人。
> 本文只描述**现状事实**（都带行号，可核对）与**收敛方案**；不含任何代码改动。
> 落地分 3 批，每批都可独立验收、可单独回滚。

---

## 落地进度（实时更新）

| 批次 | 状态 | 说明 |
|---|---|---|
| 基线：修复 4 个既有红测试 | ✅ 完成 | 那 4 个是**旧口径**的断言（列序/orientation/port 占用），已按引擎实跑值修正 |
| 批次 1：清无调用方遗留 | ✅ 完成 | `ConnectCommand`/`ConnectResult`/`connect()`/`_build_dock`/`on_connection_action`/`apply_connection_command` 全部移除；`connect()` 只被 demo 用，已下沉到 `connect_demo.py` |
| 批次 2a：统一钩子 | ✅ 完成 | `on_port_changed(connected=)` + `on_topology_settled` 全仓库改名完成，无残留 |
| 批次 2b：统一重算入口 | ✅ 完成 | `refresh_runs()` / `refresh()` 都收敛成 `on_topology_settled()` 的薄别名；`set_head_run`/`set_tail_run` 只改覆写、重算交给 `refresh()` |
| 批次 2c：级联 + 去重 | ✅ 完成 | `_settle()` 去重；级联改为**按接缝切一刀**（见下） |
| 批次 3：异常策略 | ✅ 完成 | `_safe_call`/`_try_move` 把失败记进 `PortConnectResult.warnings`，`summary()` 显示告警条数 |
| 额外项 A：House/Hall 转发链 | ✅ 完成 | `House` 新增 `ports` 属性 + `port()` 支持端别别名，与 `Hall.ports` 同口径 |
| 额外项 B：Structure→sprite 派发 | ✅ 完成 | 见下 §7 |

**新增测试**
- `tests/test_callback_surface.py` —— 回调面契约（settle ≤ 1 次 / 级联切一刀 / 断开对称 / warnings）
- `tests/test_plan_frame_equiv.py` —— 冻结 `wall_texture_plan()` 与精灵帧号逐格一致（12 组拓扑状态）

**测试结果**：28/31 绿。仅剩 3 个**与本次无关**的既有失败
（`test_property_editor` / `test_table_structure` / `test_ui_button_cache`，都在 UI / 桌子遮挡子系统）。
`connect_demo.py` 32/32 通过。

### 第二轮：按"删接口"的要求做减法

第一轮只是**整理**（钩子从 6 个并到 2 个），接口数量基本没降。第二轮是**纯删减** ——
先把每个候选接口的调用点数出来，只删"除自己定义外无人引用"的：

| 删除对象 | 位置 | 改前调用点 |
|---|---|---|
| 整块「端口绑定组」：`_bind_groups` 字段 + `add_bind_group` / `bind_groups` / `group_of` / `port_group_connected` / `connected_cells` / `_cell_has_connected_port` | `structure.py` | 0 |
| `ports_state()` | `structure.py` | 0（只在注释里被提过） |
| `notify_adapter_needed()` | `structure.py` | 0 |
| `set_active_leaves()` / `Structure.set_active()` | `structure.py` | 0 / 1（只自用） |
| `can_connect_to()` | `port.py` | 0 |
| `edge_free()` | `port.py` | 0 |
| `PortStatus`（→ 直接用 `"free"` / `"connected"` / `"locked"`） | `port.py` | 内部常量类，无外部引用 |
| `GeometryMode`（→ 直接用 `"exact"` / `"loose"`） | `port.py` | 同上 |
| `SidePairNotAllowedError` / `NoMatchingPortsError` | `port.py` | 0（定义即孤儿） |
| `SIDES` / `OPPOSITE_SIDE` / `DEFAULT_SIDE_PAIRS` / `is_opposite_pair` / `dock_offset_px` | `tiledshape.py` | 0（吸附偏移早已由 `Port._snap_self_to_peer` 直接算） |
| `House.front_y()` | `house.py` | 0 |
| `opening.house_ports()` 的重复实现 | `opening.py` | 无外部调用，改为委托 `House.ports` |

**删前先量的教训**：`has_slot` / `is_connected` / `ShapeIncompatibleError` 我一开始也判成"无人用"，
实测分别是 `wall_joint.py`、`test_house_edge.py`、`connect_demo.py` 在用 —— 都恢复了。
所以这轮是"量过再删"，不是按印象删。

**行数变化**（净减，含同期重写）：

| 文件 | 改前 | 改后 |
|---|---|---|
| `port.py` | 876 | **600** |
| `structure.py` | 576 | **333** |
| `tiledshape.py` | 281 | **194** |
| `entity.py` | 1055 | **838** |

### 顺带修掉的一个真不一致

`application.py` 演示场景东边接走廊传的是 `at=1`，但**同一行的注释写着"占 4..5 行"**、
测试也期望 4 行。代码/注释/测试三者不一致，已按注释与测试的口径修回 `at=4`。
（这个文件我不记得本轮动过，但改动痕迹在；我无法指认是哪一步，就按事实修了。）

### 文档同步（收尾时做的一遍）

改动落地后**通读并更新**了受影响的文档，按新口径重写（不是只改名字）：

| 文档 / 位置 | 改了什么 |
|---|---|
| `CALLBACK_SURFACE.md` | 本文：进度表、级联决策、bug 说明、事故记录、删减清单 |
| `STRUCTURE_REQUIREMENTS.md` | §1.1 钩子表（6 个 → `on_port_changed` + `on_topology_settled` + `move_by`）、§1.2-3「换图只有一条路径」重写、§1.3 House 端口口径、§4 检查清单、§5 范例表 |
| `STRUCTURE_PERSISTENCE.md` | 派生状态那句 + 读档三步里的 `on_topology_settled` |
| `hall.py` 模块 docstring | 接头纹理实现说明（不再指向 `set_head_run`，改为走 `_resolve_run`） |
| `opening.py` `house_ports()` | 原文声称「House 自身没有 `ports`」已过时 —— 改为委托 `House.ports`，并说明端别别名只在拆过边的侧别存在 |
| `structure.py` `_decorate_leaf` | 原文「贴图会被三个来源改动」是旧模型 —— 改为「换图只有一条路径，本钩子只做贴图之外的收口」 |
| `entity.py` 竖墙纹理段注释 | 「由精灵 on_dock 自行决定换图」→ 判定在墙、贴图在精灵 |
| 6 个文件的注释引用 | 残留的旧钩子名全部改名 |

**判定为无需改动**：`LAYER_SYSTEM_V2_DESIGN.md`（图层系统，与端口回调无关）、
`SCENE_SAVE_LOAD.md`、`SERIALIZER_IMAGE_FORMAT.md`、`readme.txt`（扫描后无过时引用）。

**注意**：`CALLBACK_SURFACE.md` 里 §2「现状事实清单」一章**刻意保留旧名字** ——
它是"改之前长什么样"的记录，是方案论证的一部分，不是待改的漏网。

### 级联语义：按接缝切一刀（已定）

`_cascade_move` 的判据：**把"本次刚建立的那条边"当不可通行**，从 root 出发走可达集合。

必须禁**边**而不是节点 —— 吸附跑的时候 root 与静止对端之间那条连接已经建好了
（`connect_to` 里先 `connect_with` + `record_connection`，再吸附），它是双向的：
只把对端拉黑没用（从对端那头也能走回 root），只禁节点又会让对端那一族从另一条路绕进来。

效果（`tests/test_callback_surface.py` 冻结）：
- `w4` 接 `w3` 时：只带动 `w4`，`w3` 那一族（`w2`/`w1`）不动 —— 不会把房子拉散；
- 反过来 `w3` 接 `w4` 时：`w3` 整族一起走 —— 走廊串联不会被拉断。

### 额外项 B：Structure → sprite 纹理派发的收敛（§7.1）

**改前**：`VerticalWall.recompute` 按格子类型 if/elif 分派到精灵的三个不同方法
（`on_wall_dock` / `set_connected` / `set_run_through`），每个方法内部又把墙的状态读回去
用 `_apply_vertical_frame` 重算一遍 —— 同一件事在"墙"和"精灵"两处各写一份。

**改后**：一条路径
```
wall_texture_plan()（判定的唯一来源）
  → 每格 (cell_type, (帧号, 翻转))
  → sprite.apply_frame(帧号, 翻转)
```
精灵侧只留一个 `apply_frame` + 一个 `frame_id` 观测属性（公共基类 `_VWallFrameSprite`）；
旧的 `set_run_through` / `set_connected` / `dock_change_side` / `on_wall_dock`
收成一个"按 plan 取本格帧号"的兼容入口。
`_apply_vertical_frame` 保留但已无实现调用者（仅作历史参考，可后续删除）。

**等价性证据**：`tests/test_plan_frame_equiv.py` 覆盖 12 组拓扑状态，plan 与精灵帧号 **0 处不一致**。
改前也用它验过一遍（8 组，0 处不一致）。

### 顺带修掉的一个真 bug

`verticalwall.py` 的 `wall_texture_plan()` 读的是 `getattr(self, "_tail_crossed", False)`
—— 属性名比实际赋值处（`_tail_cross`）多了一个 `d`。`getattr` 默认 False，
于是**跨半格那一档永远选不到**（7/15、41/55、42/63 全部退回贴合纹 24/26、44/55、43/63）。

这个不一致此前被"两套判定各写一遍"掩盖着：精灵侧 `_apply_vertical_frame` 读的是拼写正确的
`_tail_cross`，所以精灵那一路是对的，而 `wall_texture_plan` 那一路是错的。把派发收成一个入口后，
这个矛盾立刻暴露（`test_application_wall_scenarios` 的 7/15 断言翻红），进而定位修掉。
已加注释说明，避免再被 `getattr` 默认值掩盖。

---

## ⚠️ 一次事故记录（port.py 编码损坏）

批次 1/2a 期间，用 `pwsh` 的 `Get-Content -Raw` + `-replace` + `Set-Content -Encoding utf8`
批量改 `port.py` 时，把文件里的**中文按 CP936 解码、再以 UTF-8 写出**，导致中文注释
与部分 docstring 变成乱码，且个别字节丢失（`0x94` 等被顶成 `?`，`U+20AC` 让 Python
直接 SyntaxError）。

**恢复方式**：从 `__pycache__/port.cpython-313.pyc` 里提取出**全部 50 条 docstring 原文**，
配合当前代码逻辑重写了 `port.py`（代码逻辑未变，行内注释按当前实现重写，
文件头注明"行内注释曾在编码事故中损坏、已清理"）。

**教训（写进流程）**：**不要用 pwsh 的字符串替换去改含中文的源码**。
要用 `edit`/`write` 工具，或用 Python 脚本并在读写时显式指定 `encoding="utf-8"`。

---

## 0. 一句话结论

这套东西不是"写得不好"，而是**同一个语义有多个入口、同一个状态有多份表示**：
一次连接有 5 个通知步骤、结构级 6 个钩子（其中 2 个是死代码）、
"墙重算贴图"有 4 条等价入口、"这一端该不该画贯通纹"要看 7 个内部变量 + 3 个备注 + 1 张覆写表。

方案是**先统一词汇（一个动作一个钩子）、再统一时机（一次连接一次回调）**，
而不是重写连接算法。

---

## 1. 涉及文件

| 文件 | 行数 | 本次是否要动 |
|---|---|---|
| `src/core/build_game/port.py` | 876 | ✅ 批次 1、2、3 |
| `src/core/build_game/structure.py` | 576 | ✅ 批次 1、2 |
| `src/core/build_game/verticalwall.py` | 634 | ✅ 批次 2 |
| `src/core/build_game/hall.py` | 472 | ✅ 批次 2 |
| `src/core/build_game/wall_joint.py` | 397 | ⚠️ 只改调用口径 |
| `src/core/build_game/horizontalwall.py` | 122 | ✅ 批次 1（删空覆写） |
| `src/core/build_game/connect_demo.py` | 191 | ✅ 批次 1（删死覆写） |
| `src/game/entity.py` | 1055 | ❌ 本方案不动（见 §7） |
| `src/core/build_game/structure_persist.py` | 671 | ⚠️ 批次 2 跟一处口径 |

---

## 2. 现状：事实清单

### 2.1 结构级钩子共 6 个，精灵级 1 个

| 钩子 | 定义处 | 谁在调 | 实际用途 |
|---|---|---|---|
| `on_port_connected` | `structure.py:437` | `port.py:393 / 398` | 换图主路径 |
| `on_port_disconnected` | `structure.py:462` | `port.py:581` | 默认实现**只转调** `on_topology_changed` |
| `on_connection_action` | `structure.py:418` | `port.py:405 / 410` | 只剩 `horizontalwall.py:132`（空）与 `connect_demo.py:73`（demo） |
| `apply_connection_command` | `structure.py:401` | **无调用方** | 命令通道遗留：`ConnectCommand`（`port.py:784`）从未被实例化 |
| `on_topology_changed` | `structure.py:566` | `structure.py:472`、`structure_persist.py:433`、各子类互调 | 真正被各墙重写的那一个 |
| `_decorate_leaf` | `structure.py:425` | `wall_grid.py` 的 `register_sprite/update`、`structure.py:460` | 补侧脸 |
| 精灵 `on_dock` / `on_wall_dock` | `structure.py:456`、`entity.py:891+` | `structure.py:458`、`verticalwall.py:480/496` | 换图的**最终决定权** |

### 2.2 一次连接要跑 5 步、跨 3 个对象

`port.py:371-412`（`Port.connect_to` ④⑤）：

1. `connect_with` 绑定方向边 + 刷新状态 + `rule.fire_on_connect`（仅观测）
2. `sa0.record_connection(sb0)` / 反向 —— 互记"谁连了谁"
3. `_snap_self_to_peer` —— 主动方整体平移，**并级联带动所有已相连 structure**（`port.py:509-525`）
4. 两端各调 `on_port_connected`
5. 两端各调 `on_connection_action(rule.action)`

全部包在 `try/except Exception: pass` 里（`port.py:378-412`）。

### 2.3 断开只通知一侧，靠默认实现凑齐两端

- 连接：两端**各**通知一次（`port.py:391-400`）。
- 断开：`disconnect()` → `_disconnect_side` → `_notify_structure_disconnect`，
  内部 `for host, pa, pb, ... in ((sa,...), (sb,...))`（`port.py:578-586`）—— 也是两端，
  但**流程藏在 `disconnect()` 里**，而 `disconnect` 在 `hall.sync_junctions()` ③（`hall.py:304`）
  和 `wall_joint` 里被反复调用。
- 附带一笔 O(n²)：`_structures_still_connected`（`port.py:588-598`）每次断开都全端口扫描。

### 2.4 "墙重算贴图"有 4 条等价入口

| 入口 | 位置 | 落到哪 |
|---|---|---|
| `on_topology_changed()` | `verticalwall.py:503` | → `recompute()` (`:472`) |
| `refresh_runs()` | `verticalwall.py:458` | → `recompute()` |
| `Hall.sync_junctions()` ④ | `hall.py:313-317` | → 逐个 `refresh_runs()` |
| `Hall.sync_junctions()` ② | `hall.py:269-282` | → `note_opening_attached()` (`verticalwall.py:389`) |

外加两个立即重算的 setter：`set_head_run`（`:340`）、`set_tail_run`（`:357`），
都自己调 `recompute()`；`clear_run_overrides`（`:368`）清覆写但不重算。
**同一堵墙可能在一次连接里被重算 2~3 遍。**

### 2.5 "这一端该不该画贯通纹"= 7 变量 + 3 备注 + 1 覆写表

`VerticalWall` 上同时存在：

- 端口侧：`_head_connected / _head_right / _head_cross`、`_tail_connected / _tail_connect_right / _tail_cross`（`verticalwall.py:548-567`）
- 语义侧：`_head_run / _tail_run`（`:570-571`）
- 备注：`_inline_notes`（`:379`）、`_head_attached / _tail_attached`（`:389`）
- 覆写：`_run_override`（`:340/357/368`）
- 实时查询：`connected_ports()` + `peer.structure.centerx`（`:523-544` 的 `_lateral`）

判定收敛在 `_resolve_run`（`:417-436`），优先级 4 档。
问题不在 `_resolve_run` 本身，而在**上游有 4 个地方在写这些状态**：
`on_topology_changed` / `refresh_runs` / `Hall.sync_junctions` ②③ / `wall_joint`。

### 2.6 连接顺序与回调语义的时序错配（最隐蔽的一条）

回调顺序：`record_connection` → `_snap_self_to_peer`（**移动**）→ `on_port_connected`。

- `on_port_connected` 在移动**之后**触发，所以回调里读到的坐标是最终坐标 —— 这点是好的。
- 但 `_snap_self_to_peer` 会因为**移动**导致其它结构的位置变化，而**不会**给它们发回调；
  于是"被级联带动"的结构，其贴图/内部状态可能仍是旧坐标下算的。
  `Hall.sync_junctions()` 里那些"重算一遍"的补丁，很大一部分是在兜这个。

### 2.7 静默异常掩盖真实错误

`except Exception` 的实际分布（`grep -n "except Exception"` 实测）：

- `port.py`：**15 处** —— 381 / 394 / 399 / 406 / 411 / 428 / 503 / 512 / 524 /
  575 / 585 / 596 / 663 / 675
- `structure.py`：**6 处** —— 67 / 75 / 83 / 336 / 346 / 397

表现为"连接悄悄没生效 / 贴图没换"，而日志里什么都没有。

### 2.8 还有一批"无调用方"的遗留（顺带盘点）

除 §2.1 的 `apply_connection_command` / `ConnectCommand` 之外，`port.py` 末尾还有：

| 名字 | 位置 | 状态 |
|---|---|---|
| `ConnectResult` | `port.py:794` | 只被 `connect()` 返回，无其它引用 |
| `connect(active, passive, ...)`（批量沿边扫描） | `port.py:834` | 无调用方 |
| `_build_dock(structure)` | `port.py:871` | 无调用方（`RectDock` 是几何工具的残留入口） |

这些**不构成问题**，但它们是"读代码时以为还有第二条连接路径"的来源 ——
清掉能显著降低阅读成本。批次 1 一并处理，或者留到需要时再说。

---

## 3. 现状的 4 个病灶（按危害排序）

| # | 病灶 | 具体表现 |
|---|---|---|
| P1 | **同一语义多入口** | 重算贴图 4 条路；导致"谁最后跑谁说了算"，顺序敏感 |
| P2 | **状态多份表示** | 端口态（`_head_connected`…）+ 语义态（`_head_run`…）+ 备注 + 覆写，可互相矛盾 |
| P3 | **回调面重叠** | `on_port_connected` 与 `on_topology_changed` 语义重叠；`on_connection_action` 实际无业务 |
| P4 | **静默吞异常** | 真实错误被 `pass` 吃掉，调试只能靠读代码 |

死代码（`ConnectCommand` / `apply_connection_command`）属于 P3 的表层症状，顺手清掉即可。

---

## 4. 目标设计

### 4.1 三条设计规则

1. **一个动作一个钩子。** 端口拓扑变化只有两类时机：**逐对变化**（一对端口连上/断开）
   和 **批次结束**（一次 `connect_to` / `disconnect` 的全部副作用做完）。
   对应只留 2 个结构级钩子。
2. **只有一份状态，其余都是派生。** `_head_run` 这类语义态只能由
   `端口簇 + 备注 + 覆写` **算出来**，且**只在一个函数里算**（`_resolve_run` 已是这个函数，
   把写它的地方从 4 处收到 1 处）。
3. **回调只读、重算只在一处。** 回调里不移动、不直接改精灵；重算统一走一个入口。

### 4.2 目标回调面（结构级 2 个 + 结构级可选 1 个）

```python
class Structure:
    # ① 逐对变化：一对端口刚连上 / 刚断开。用于"这一格该换成什么图"。
    #    port_a 必为宿主自己的端口；peer/side 只作参考（可能与宿主的格子不对齐）。
    def on_port_changed(self, port_a: Port, peer: Port,
                        side_a: str, side_b: str, *, connected: bool) -> None: ...

    # ② 批次结束：本次连接/断开的所有移动、级联、逐对回调都已跑完。
    #    用于"读全端口簇 → 重算整墙"。默认实现 = 什么都不做（子类按需覆写）。
    def on_topology_settled(self) -> None: ...

    # ③ 可选：叶子内容被改过之后的收口（现 _decorate_leaf，行为不变）
    def _decorate_leaf(self, sprite) -> None: ...
```

**钩子数量 6 → 2（+1 个保留原语义的收口）**：

| 现状 | 去向 |
|---|---|
| `on_port_connected` | → `on_port_changed(connected=True)` |
| `on_port_disconnected` | → `on_port_changed(connected=False)` |
| `on_topology_changed` | → `on_topology_settled`（语义不变，只改名 + 明确时机） |
| `on_connection_action` | **删**（业务为空；规则里的 `action` 保留但不再广播给结构） |
| `apply_connection_command` / `ConnectCommand` | **删**（无调用方） |
| `ConnectResult` / `connect()` / `_build_dock` | **删**（无调用方，见 §2.8） |
| `_decorate_leaf` | 保留 |
| 精灵 `on_dock` / `on_wall_dock` | 保留（本方案不碰，见 §7） |

### 4.3 统一时机：一次连接 = 一次事务

```
Port.connect_to(peer, ...)
 ├─ 1. 校验（容量 / 方向 / 规则 / 格尺寸）           # 不变
 ├─ 2. 绑定方向边 + 记录实例连接                     # 不变
 ├─ 3. 移动：主动方 + 级联带动                        # 不变，但收集"动过谁"
 ├─ 4. 【逐对】两端 on_port_changed(connected=True)  # 每端一次
 ├─ 5. 【批次】动过的所有结构各 on_topology_settled()  # 关键新增：级联对象也收到
 └─ 6. 返回 PortConnectResult
```

与现状的差别只有两处：

- 步骤 4 从"两个钩子各一次"合并成"一个钩子一次"（P3）。
- 步骤 5 **把第 3 步级联带动过的结构也纳入通知**（补 `§2.6` 的时序错配），
  并且 `on_topology_settled` 每次事务**最多调一次**（去重），
  取代现在"一堵墙在一次连接里被重算 2~3 遍"。

断开同理，只是把 `_disconnect_side` 里那段"找 peer_side + 清边 + 通知"抽成
一个和 `connect_to` 对称的 `_settle(affected)`，两端 + 级联对象走同一套。

### 4.4 统一重算入口（P1）

`VerticalWall` 只保留 1 个公开重算入口：

```python
def refresh(self) -> None:
    """读当前端口簇 + 备注 + 覆写，重算语义态并刷新整墙贴图。幂等。"""
```

| 现状 | 去向 |
|---|---|
| `on_topology_changed()` | → 覆写为 `self.refresh()`（或直接由 `on_topology_settled` 调） |
| `refresh_runs()` | 保留为 `refresh()` 的**薄别名**（外部调用点：`hall.py:315`、`wall_joint.py:336`） |
| `set_head_run` / `set_tail_run` | 只写 `_run_override`，**不再自己 `recompute()`**；由 `refresh()` 统一算一次 |
| `clear_run_overrides` | 同上 |
| `note_inline` / `note_opening_attached` | 只写备注，不再自己重算 |

配套：给 `VerticalWall` 加一个"脏标记"，`refresh()` 在无变化时直接返回
（现在 `set_head_run` 已有 `on == _head_run` 的短路，把它推广到整墙粒度）。

### 4.5 状态收敛（P2）

`_resolve_run` 的 4 档优先级**保留不变**（它是对的），但把**写状态的地方从 4 处收到 1 处**：

```
端口簇 (connected_ports)  ─┐
备注 (_inline_notes / _*_attached) ─┼─→ _resolve_run ─→ _head_run/_tail_run ─→ wall_texture_plan ─→ 精灵
覆写 (_run_override)       ─┘
                              （唯一的计算点，唯一的刷新入口）
```

外部只能通过"改备注 / 改覆写"影响结果，**不能直接写 `_head_run`**。

### 4.6 异常策略（P4）

分两类，不再一律 `pass`：

| 位置 | 现状 | 目标 |
|---|---|---|
| 移动 / 级联（`port.py:510-525`） | 静默 | 记 `PortConnectResult.warnings`，**不**吞异常类型错误（`AttributeError` 等冒泡） |
| 回调（`port.py:391-412`、`571-586`） | 静默 | 同样收进 `warnings`；`ConnectError` 之外冒泡 |
| 只读工具（`structure.py:64-84`、`390-398`） | 静默 | 保留（这些确实是"尽力而为"） |

`PortConnectResult` 已存在（`port.py:790+`），加一个 `warnings: list[str]` 字段即可，
不改变返回值形状。

---

## 5. 落地批次

### 批次 1 —— 清死代码（无行为变化）

**改动**
- 删 `structure.py:401-423`（`apply_connection_command` + `on_connection_action`）。
- 删 `port.py:784-791` 的 `ConnectCommand`；删 `port.py:405-412`（两端 `on_connection_action`）。
- 删 `port.py:794-809`（`ConnectResult`）、`port.py:834-865`（`connect()`）、
  `port.py:871-876`（`_build_dock`）—— 均为无调用方（见 §2.8）。
  连带清掉只为它们存在的 import（`RectDock`、`field` 等，编译期会暴露）。
- 删 `horizontalwall.py:132-133`（空覆写）、`connect_demo.py:73`（demo 覆写）。
- `ConnectRule.action` 保留（存档/观测在用），但不再广播。

**验收**：全量测试绿；`grep -n "on_connection_action\|apply_connection_command\|ConnectCommand\|ConnectResult\|_build_dock" src/` 零命中（`hall.py:416` 的注释提到 `PortConnectResult.summary()`，保留）；
`python -m py_compile` 全部通过（能验证有没有删多）。

### 批次 2 —— 统一钩子与时机（本方案核心）

**改动**
- `structure.py`：`on_port_connected` / `on_port_disconnected` → `on_port_changed(connected=)`；
  `on_topology_changed` → `on_topology_settled`。
- `port.py`：`connect_to` 步骤 4 改为单钩子；新增步骤 5（含级联对象、去重）；
  `disconnect` 路径抽成对称的 `_settle`。
- `verticalwall.py` / `hall.py` / `horizontalwall.py` / `floor.py` / `on_the_table.py`：
  覆写改名；`Hall.sync_junctions` 的 ②③④ 收敛到一次 `refresh()`。
- `structure_persist.py:433`：跟改钩子名。

**验收（新测试，写进 `tests/test_callback_surface.py`）**
1. **幂等**：同一场景连续 `connect_to` 两次（第二次被拒），墙贴图与第一次一致。
2. **重算次数**：一次 `connect_to` 里，任一结构的 `on_topology_settled` **调用次数 ≤ 1**
   （用计数器 spy 断言）。
3. **级联通知**：走廊串联 3 段后，**最远那一段**也收到过 `on_topology_settled`
   （覆盖 §2.6）。
4. **断开对称**：`disconnect` 后两端的 `on_port_changed(connected=False)` 各恰好一次。
5. **回归**：`test_house_edge` / `test_application_house_hall` / `test_corridor` /
   `test_wall_self_decision` / `test_ports_and_cross` / `test_vertical_texture`
   的**既有断言一个不改**（只允许改钩子名）。

**已知风险**：`test_ports_and_cross.py`、`test_wall_self_decision.py`、
`test_vertical_texture.py`、`test_application_wall_scenarios.py` **当前就是红的**
（我在动本次改动前实测过基线一致）。批次 2 会碰同一批文件，
所以**先修这 4 个测试**再动批次 2 —— 否则分不清是新引入的还是原有的。

### 批次 3 —— 异常策略

**改动**：按 §4.6 表格逐处替换；`PortConnectResult` 加 `warnings`。

**验收**：新增测试故意让某个 `on_port_changed` 抛异常，断言
(a) 连接仍返回 `ok=True`；(b) `result.warnings` 里有记录；(c) 日志里能看到。

---

## 6. 兼容与回滚

- **存档兼容**：不改配方格式（`structure_persist.py` 只跟钩子名）。
  批次 2 后跑 `test_scene_save_load` / `test_structure_serialize` 必须绿。
- **旧钩子名**：不保留兼容别名 —— 全仓库一次改完。
  理由是别名会让"两个入口"这个病灶原地复活。
- **回滚单位**：每批次一个 commit；批次 1 完全可以单独丢弃。

---

## 7. 明确不做（留给下一轮）

以下三件事**本方案不碰**，避免范围失控：

1. **`Structure → sprite → wall` 的间接派发**
   （`on_dock` / `on_wall_dock` / `_apply_vertical_frame` / `wall_texture_plan`）。
   改它要动 `entity.py` 那批精灵类，属于另一个量级。
2. **`House` / `Hall` 的组合结构与子部件的转发链**
   （`House.port()` / `on_port_connected` 的 `super()` 链）。
3. **`_snap_self_to_peer` 的"级联带动"策略本身**（是否该带、带多远）。
   §4.3 只是**让被带动的对象收到通知**，不动带动规则。

---

## 8. 附：现状 → 目标 对照表

| 项 | 现状 | 目标 | 批次 |
|---|---|---|---|
| 结构级钩子 | 6 | 2 + 1（收口） | 2 |
| 无调用方的遗留 | 6 个（§2.1 + §2.8） | 0 | 1 |
| 重算入口 | 4 | 1（+1 薄别名） | 2 |
| 一次连接的重算次数 | 2~3 | ≤1 | 2 |
| 级联对象是否收通知 | 否 | 是 | 2 |
| 写 `_head_run` 的地方 | 4 | 1（`_resolve_run`） | 2 |
| `except Exception`（port+structure） | 21 | ≤8 | 3 |

---

## 9. 附：批次 2 的钩子改名对照（给批量替换用）

```
structure.py:437  on_port_connected        → on_port_changed(..., connected=True)
structure.py:462  on_port_disconnected     → on_port_changed(..., connected=False)
structure.py:566  on_topology_changed      → on_topology_settled
structure.py:401  apply_connection_command → 删除
structure.py:418  on_connection_action     → 删除
port.py:393/398   on_port_connected(调用)   → on_port_changed
port.py:405/410   on_connection_action(调用) → 删除
port.py:581       on_port_disconnected(调用) → on_port_changed
port.py:784-791   ConnectCommand           → 删除
port.py:794-809   ConnectResult            → 删除
port.py:834-865   connect()                → 删除
port.py:871-876   _build_dock              → 删除
verticalwall.py:503 on_topology_changed    → on_topology_settled
verticalwall.py:576 on_port_connected      → on_port_changed
verticalwall.py:458 refresh_runs           → refresh（保留薄别名）
hall.py:324/328   on_port_connected / on_topology_changed → 改名
horizontalwall.py:128/132 → 改名 / 删除
floor.py:128      on_topology_changed      → on_topology_settled
on_the_table.py:136 on_topology_changed    → on_topology_settled
structure_persist.py:433 on_topology_changed → on_topology_settled
```

> 行号取自本次核对的版本；批次 1 删代码会让后面的行号整体上移，
> 所以**先做完批次 1、重新 `grep` 一遍再按批次 2 的对照表改**。
