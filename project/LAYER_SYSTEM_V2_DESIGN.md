# Layer 系统 v2 设计（定稿评审稿，只读，不包含实现）

> 目标读者：本人（维护者）。本文定方向、接口与迁移清单，**本轮不写实现代码**。
> 依据评审已定稿（见 §2 / §6）。评审意见会直接改到这里。

> ## 实施状态（自动更新）
> - ✅ 阶段 1：`src/utils/layer_v2.py`（`LayerKind/LayerSpec/LayerBucket/LayerStack/Renderable/from_tmx`）+ `tests/test_layer_v2.py`（通过）。
> - ✅ 阶段 3：`settings.py` 加 `ENABLE_DEFAULT_LAYER/ENABLE_TEST_LAYER/LAYER_DEFAULTS`；`layer_v2.configured_special_specs()`（default 与 test 各自独立开关）。
> - ✅ 阶段 2：`GameWorld.layers = LayerStack`；**删除 `src/utils/layer.py`(Layer/LayerManager)**；import 统一到 `src.utils.layer_v2`（唯一路径）。
> - ✅ 阶段 4：opengl 渲染器遍历 LayerStack 桶；pygame 渲染器删硬编码层名改 `iter_visible()`+`leaf_sprites_of`；`Structure.iter_sorted_leaf_sprites()`。
> - ✅ 阶段 5：`editor_ui` 订阅 `world.layers` 重建图层按钮（contents 级不重建）；hover 跳过无 world_position 的组合对象。
> - ✅ 阶段 6：`setup.get_tmx_layers` 返回 `LayerSpec`；application 在引擎层之上叠加地图层并往 `test` 桶加墙。
> - ⏳ 阶段 7：真实默认后端为 **OpenGL**，无法在无头环境验证 —— **需在本地跑 `main.py`(opengl) 做视觉回归**；headless(pygame 后端) 已 BUILD_OK+FRAMES_OK。
>
> 运行时权威图层集合（headless 实测）：`terrain, House, Groud, Water, Hill, HouseFloor, BgWall, FgWall, Furniture, Decoration, characters, objects, collision, effects, default, test`。
>
> ### 后续收尾（已落）
> - `settings.ENABLE_DEFAULT_LAYER / ENABLE_TEST_LAYER` 改为 `Switch`(Command 名 `EDL/ETL`)，default 与 test 各自独立开关。
> - `Structure.is_collidable = False` 默认：杜绝被 `add_game_object` 误加进物理产生 (0,0) 幽灵碰撞；逐格碰撞属未来“structure 碰撞编辑器”。
> - `Structure` 基类新增只读 `world_position`(所有叶子外包矩形)、active 控制 `set_active(active, propagate_to_leaves=True)`(默认统一传播) / `set_active_leaves(active)`(逐格)。
> - 编辑器 hover/删除继续跳过组合对象(Structure) —— 属性面板/删除整墙待定。
>
> ### 第二轮收尾（已落，headless 验证）
> - **编辑器整墙删除**：命中判定不用整墙外接矩形，而是**逐格叶子矩形**精确判定——点中任一真实格子即命中**整面墙**，一次删净(连同从空间网格注销)。见 `editor_manager._hits/_remove_object`。
> - **Structure↔block 整合**：demo 竖/横墙改经 `world.add_game_object` 进 `test` 层 → 用 `world_position`(bbox)注册进空间网格，随视口激活而 `in_active_block` 显隐(headless 实测 3 墙随视口 全灭/全活)。
> - **block 轻量化重构**:`world.update_blocks` 改为**只在“活跃对象集合”变化时才翻转标志**(维护上一帧活跃对象集,idle 时 O(1) 集合比较返回)。headless 实测:墙随视口 3→0 灭、再回 3 活;idle 二次调用开销≈0;新放置对象在视口内能点亮。

---

## 0. 一句话目标

把"图层"从 **一个既当描述符、又当精灵容器、又藏在假单例里的混血对象**，拆成 **声明式描述符(LayerSpec) + 每世界一实例的层栈(LayerStack) + 统一渲染顺序协议**，让 UI、渲染器、编辑器都读**同一份真值源**，不再各自快照/各自排序。

---

## 1. 现状痛点（代码依据）

| # | 问题 | 现状位置 |
|---|------|---------|
| P1 | **假单例**：`LayerManager` 状态存在类属性 `cls.layers`，但被 `world.layers = LayerManager()` 当实例用；所有世界/模块共享同一个全局 dict | `src/utils/layer.py`（classmethods + 类属性）<br/>`src/game/world.py:44` |
| P2 | **双 import 路径**：有的 `from utils.layer import`，有的 `from src.utils.layer import`，可能加载成两个模块对象 → 两套独立 LayerManager | `editor_manager.py:12`、`opengl_renderer.py:13`（`utils.layer`）；`world.py:29`、`application.py:14` 等（`src.utils.layer`） |
| P3 | **渲染器不读 manager 顺序**：Pygame 后端硬编码层名列表，两后端画哪层/按什么序不一致 | `pygame_renderer.py:56` vs `opengl_renderer.py:333` |
| P4 | **UI 一次性快照按钮**：`_create_layer_buttons` 创建时读一次 `keys()`，层变更不刷新；点击按 `list(keys())[i]` 取 index → 下标漂移 | `src/ui/editor_ui.py:56-61,91-97` |
| P5 | **编辑器 hover/放置读类级单例**，与 `world.layers` 是另一引用 | `editor_manager.py:70,233`（`LayerManager.values()`/`layers['effects']`） |
| P6 | **Structure 自带另一套分层语义**，OpenGL 靠 `isinstance(obj, Structure)` 硬判才走 y-sort | `structure.py`；`opengl_renderer.py:352-360` |
| P7 | **初始化时序混乱**：`GameWorld.__init__` 先 `set_layers()` 清空再放对象，application 又 `set_layers(tmx)`+`clear_all()`；因 P1 全局共享，一处清层殃及所有 world | `world.py:44-45,101-107`；`application.py:46` |

P1/P2 根因；P3/P4/P5 是"不读同一真值源"的症状；P6 是需要保留复用的 Structure 语义。

---

## 2. 定稿决策（本轮评审已确认）

1. **每世界一个真实层栈**：`world.layers` 是 `LayerStack` **实例**，状态在实例上（非类属性），世界之间互不影响。
2. **彻底删除 `LayerManager`**：不保留、不冻结为兼容层。
3. **import 统一到 `src.utils.layer`**：全仓只此一条路径，并加"双份加载即抛错"的防御断言。
4. **精灵容器保留 `pygame.sprite.Group`**：每层桶继续用 Group 承接现有 Sprite 生态。
5. **兜底层只由配置建立**：见 §5 —— `default`(兜底) 与 `test`(测试) 是"特殊层"，在 settings 声明、程序初始化时决定是否建立。
6. **Layer 只做严格按层序的简单确定性排序**：不做跨层遮挡魔法；遮挡正确性靠把对象放进正确的层/结构(Structure)。
7. **复杂遮挡交给 Structure**：一切"一组的、会互相遮挡的"复合物（墙体、树冠、屋顶、建筑）都走 Structure，它在内部按深度/偏移处理自身精灵。
8. **地形语义**：真·地形（地面/纯背景）**永远垫底、永不遮挡、不参与交互**；树、屋顶这类会遮住角色的**不是地形，是 OBJECT**。

---

## 3. 核心模型：三层分离

```
地图/关卡定义  ──►  LayerSpec（声明式、不可变、无精灵）   真值源 A：层“长什么样”
世界运行时    ──►  LayerStack（每 GameWorld 一个实例）    真值源 B：当前世界有哪些层、顺序、可见性
                     └─ 每层一个 LayerBucket = pygame.sprite.Group
渲染/UI/编辑  ──►  只订阅 LayerStack，不自持层名副本
```

### 3.1 `LayerKind`

```python
class LayerKind(Enum):
    TERRAIN = "terrain"   # 纯地面/背景：垫底、不 y 排、不交互、不遮挡
    OBJECT  = "object"    # 普通对象层（含树/屋顶/墙体等会遮挡的东西）
    EFFECT  = "effect"    # 特效/覆盖层（最上）
```

- **地形语义的关键区分**：导入 TMX 时**不能把图层一概当地形**。只有 `kind=TERRAIN` 的层是垫底底板；树/屋顶/墙等 TMX 里会立起来的元素要归到 `kind=OBJECT` 的层（或包成 Structure）。
- 分类依据：TMX 层的自定义属性 `kind`（缺省按名字/用途推断，见 §8）。

### 3.2 `LayerSpec`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class LayerSpec:
    name: str                      # 唯一 key
    order: int = 0                 # 层序（Layer 唯一排序机制，见 §6）
    floor: int = 0
    kind: LayerKind = LayerKind.OBJECT
    visible: bool = True
    opacity: float = 1.0
    y_sorted: bool = False         # 层内普通精灵是否按世界 Y 排（便利项，非遮挡主解）
    physics: bool = False          # 该层对象默认入物理/网格
    synthetic: bool = False        # True = 特殊层(default/test)，见 §5
    source: str | None = None      # "tmx:<name>" 等溯源

    def with_visible(self, v: bool) -> "LayerSpec": ...
    def with_order(self, o: int) -> "LayerSpec": ...
```

- 不可变；改动返回新副本。
- `TERRAIN` 层建议 `y_sorted=False`（纯底板按插入/层序即可）。
- `OBJECT` 层里若放了"会互相遮挡的普通精灵"，用 `y_sorted=True` 做层内 y 排；真正复杂/需要跨层遮挡的还是包成 Structure。

### 3.3 `LayerBucket`

```python
class LayerBucket:
    spec: LayerSpec
    sprites: pygame.sprite.Group   # 决策 4：保留 Group

    def add(self, obj): ...
    def remove(self, obj): ...
    def __iter__(self): ...
```

### 3.4 `LayerStack`（每世界实例，真值源）

```python
ChangeKind = "add" | "remove" | "reorder" | "visible" | "contents"

class LayerStack:
    def __init__(self, specs: Iterable[LayerSpec] = ()) -> None: ...

    def add_layer(self, spec: LayerSpec) -> None        # 同名=更新
    def remove(self, name: str) -> None
    def reorder(self, name: str, order: int) -> None
    def set_visible(self, name: str, v: bool) -> None

    def get(self, name: str) -> LayerBucket | None
    def iter_visible(self) -> Iterator[LayerBucket]     # 按 order 稳定序
    def names(self) -> list[str]
    def __getitem__(self, name: str) -> LayerBucket
    def __iter__(self) -> Iterator[LayerBucket]
    def __contains__(self, name: str) -> bool
    def __len__(self) -> int

    def add(self, name: str, obj) -> None               # 统一入层入口
    def remove_object(self, obj) -> LayerBucket | None
    def find_bucket_of(self, obj) -> LayerBucket | None

    def subscribe(self, cb: Callable[[ChangeKind, str | None], None]) -> None
    def unsubscribe(self, cb) -> None
    def notify(self, kind: ChangeKind, name: str | None) -> None
```

- 每世界独立；`add/remove/reorder` 只影响本世界。
- `subscribe` 让 UI/hover 缓存/渲染器成为观察者，不再自己快照。
- `find_bucket_of` / `remove_object` 把"对象在哪个层"纳入单一真值源。

---

## 4. 兜底 / 特殊层配置（决策 5）

在 `settings.py` 声明哪些"特殊层"要在初始化时建立：

```python
# settings.py
LAYER_DEFAULTS = {
    "default": {"kind": "object", "order": 100, "synthetic": True},  # 兜底层
    "test":    {"kind": "object", "order": 200, "synthetic": True},  # 测试专用层
}
# 程序启动时由启动器依据配置建层（不写死在 GameWorld 里）
```

建层逻辑（启动器/`GameWorld` 初始化，由 settings 驱动）：
- 遍历 `LAYER_DEFAULTS`，把 `synthetic=True` 的层加进 `LayerStack`。
- **`default` 层决定"未知层名的兜底策略"**：
  - 若建了 `default` 层 → `LayerStack.add(name, obj)` 遇到未知 `name` 时**宽松回落**到 `default` 桶（可打 warning，便于追查）。
  - 若**没有** `default` 层 → 遇到未知 `name` **直接抛显式错误**（`KeyError`/自定义 `UnknownLayerError(name)`），不再静默隐式建层。

`test` 层仅供测试/调试对象使用，正式对象不应落进去。

> 一句话：兜底宽严由"是否建了 default"决定——宽松则回落，严格则抛错。

---

## 5. UI 同步（解决 P4）

- `EditorInterface` 构造时 `self.world.layers.subscribe(self._on_layer_changed)`。
- `_on_layer_changed(kind, name)` 只重建图层按钮（工具/模式按钮与层无关不动）：
  ```python
  def _on_layer_changed(self, kind, name):
      self.layer_buttons.clear()
      for i, bucket in enumerate(self.world.layers):       # 从真值源读
          btn = UIButton(pygame.Rect(210, 30+i*40, 130, 30),
                         bucket.spec.name.capitalize())
          btn.tag = bucket.spec.name                        # 按钮携带层 key
          self.layer_buttons.append(btn)
      # 若删的正是当前层，回落/清空选中
      if self.editor.selected_layer not in self.world.layers:
          self.editor.select_layer(None)
  ```
- 点击时用 `btn.tag` 直取层 key，`editor.select_layer(btn.tag)`，**去掉 `list(keys())[i]` index 反查** → 杜绝下标漂移。
- 可选增强：每层按钮带眼睛图标切换 `set_visible`。

---

## 6. 渲染顺序协议（决策 6/7/8 定稿）

### 6.1 分工原则（最重要的定稿）

| 谁负责 | 做什么 | 说明 |
|---|---|---|
| **LayerStack** | 严格按 `order` 提供层序，UI/渲染只消费这份顺序 | Layer 系统**不解决跨层遮挡** |
| **LayerSpec.y_sorted** | OBJECT 层内对普通精灵做 Y 排 | 便利项，非遮挡主解 |
| **Structure** | 一组会互相遮挡的精灵，内部按深度/偏移排序展开 | **复杂/跨层遮挡的唯一正确解** |

> 结论：Layer 层面不去做"全局 y-sort / isinstance 硬判"。把东西放进正确的层，遮挡交给 Structure。

### 6.2 层 → 绘制带

```python
def render_world(renderer, stack):
    for bucket in stack.iter_visible():          # 层本身严格按 order 稳定序
        spec = bucket.spec
        if spec.kind == LayerKind.TERRAIN:
            draw_bucket(renderer, bucket, y_sorted=False)   # 垫底底板，不 y 排
        elif spec.kind == LayerKind.EFFECT:
            draw_bucket(renderer, bucket, y_sorted=False)   # 最上特效
        else:  # OBJECT
            draw_bucket(renderer, bucket, y_sorted=spec.y_sorted)
```

- TERRAIN 垫底、按层序与桶内插入序绘制，永不参与遮挡。
- OBJECT 层：默认按桶内序；`y_sorted=True` 时桶内普通精灵按世界 Y 排一次（仍**只在本层内**）。
- EFFECT 叠最上。

### 6.3 对象侧绘制契约（Structure 接入）

```python
class Renderable(Protocol):
    def iter_sorted_leaf_sprites(self):   # 组合对象展开叶子；普通对象默认 [self]
```

- 普通 Sprite → `[self]`（保留 Group/单精灵路径）。
- **Structure**（决策 7）：作为**一个整体**占一个桶成员；渲染时经 `iter_sorted_leaf_sprites()` 展开，内部调用它已有的 `get_sprite_order()`（已含 `layer_offset`/`RenderOrder` 的内部深度）。Layer 不需要知道 Structure 内部有多少格子，也不需要 isinstance 硬判——只要它满足 `Renderable` 就统一展开。
- 两个后端走同一套：Pygame 后端也走展开契约（现状它直接 `blit(obj.image)`，Structure 无单一 `image`，必须改走展开，见 P3）。

---

## 7. Structure / build_game 兼容

按决策 7：
- 不改 `structure.py` 的内部连接/端口/深度排序逻辑。
- 基类补 `iter_sorted_leaf_sprites()` 返回 `get_sprite_order()`，把自己声明为 `Renderable`。
- Structure 整体归属到一个 OBJECT 层（如 `"build"` 层）的桶；hover/删除/属性面板把它当**一个对象**处理，不逐格散开。
- `obj.effects_group`（现绑 `LayerManager.layers['effects']`，`editor_manager.py:233`）改绑 `world.layers['effects']` 桶。

---

## 8. TMX / 存档映射

- **建层**：`LayerSpec.from_tmx(tmx_layer)`：
  - 读自定义属性 `kind`（`terrain`/`object`）→ `LayerKind`；
  - 树/屋顶/墙等会遮挡的层即使来自 TMX 也标成 `kind=object`（决策 8）；
  - 读 `order/floor/visible/opacity`，回填 `source="tmx:<name>"`。
- **运行时**：TMX 定义 → 启动器把 `[LayerSpec...]` 交给 `world.set_layers(specs)`（内部 `self.layers = LayerStack(specs)`），再叠加 settings 的特殊层（§4）。
- **存档**：serializer 打 type 标签时取 `LayerBucket.spec.name`（现在 `ui_manager.save_func` 遍历 `layer.name`，逻辑不变，来源换 spec）。
- **幽灵清理**：`clear_all` 只清当前世界 `self.layers`，不波及全局。

---

## 9. 分阶段迁移清单

> 每阶段可独立验证，小步走。

- **阶段 0 · 冻结基线**：记录 `main.py`（OpenGL 后端默认）为可运行基线。
- **阶段 1 · 新增 v2 模块**（只加文件不改旧行为）：`src/utils/layer_v2.py` 实现 `LayerKind/LayerSpec/LayerBucket/LayerStack`（含 subscribe）+ `Renderable` 协议 + `LayerSpec.from_tmx`。单测：增删改/稳定重排/订阅回调/双栈互不影响/兜底策略。
- **阶段 2 · GameWorld 切到 LayerStack**（灭假单例）：`world.py` 改 `self.layers = LayerStack()`；`set_layers/add_game_object/remove_game_object/clear_all` 从实例读。同步把 `editor_manager.py`、`opengl_renderer.py` 的 import 统一为 `src.utils.layer`，加防御断言。**删除 `LayerManager`。**
- **阶段 3 · settings 特殊层**：`settings.LAYER_DEFAULTS` + 启动建 default/test；`add` 兜底/抛错策略落地。
- **阶段 4 · 渲染顺序协议统一**：两渲染器 `_draw_world` 走 `iter_visible()` + `Renderable`；Pygame 后端删硬编码层名并补 Structure 展开路径；`Structure` 基类补 `iter_sorted_leaf_sprites`。
- **阶段 5 · UI 订阅层变更**（解决 P4）：`editor_ui.py` subscribe 重建；按钮 tag 点击；`editor_manager.py` hover/放置/effects 改读 `world.layers`。
- **阶段 6 · TMX/存档/application 接线**：`setup.py` 用 `LayerSpec.from_tmx`（kind 分类）；application 用 specs 初始化并叠特殊层；存档取 `spec.name`。
- **阶段 7 · 回归**：两后端各跑编辑/放置/删除/存档读档；结构墙归属 build 层、hover/删除、内部深度绘制正常；default/test 开关两种配置都验证。

---

## 10. 评审结论存档

| 取舍点 | 结论 |
|---|---|
| 隐式建 default 层 | 取消静默隐式建层 → 改由 `settings.LAYER_DEFAULTS` 决定建 default/test；有无 default 决定兜底"回落 vs 抛错"（§4） |
| 全局 y-sort vs 层内 y-sort | Layer 只严格按层序；层内 `y_sorted` 便利；复杂遮挡交给 Structure（§6.1） |
| `LayerManager` 去留 | **彻底删除** |
| `utils.layer` vs `src.utils.layer` | 统一 `src.utils.layer`，加双份加载断言 |
| 保留 pygame Group | **保留** `pygame.sprite.Group` 作每层容器 |
| 地形语义 | TERRAIN 纯底板永不遮挡；树/屋顶 = OBJECT（可走 Structure） |

---

*设计已按评审定稿。§9 迁移清单随时可按阶段实施；如需我先动阶段 1（新增 layer_v2 模块，不改旧行为），说一声即可。*
