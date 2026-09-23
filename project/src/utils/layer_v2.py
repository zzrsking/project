"""Layer v2 —— 声明式层描述 + 每世界一实例的层栈。

设计依据：LAYER_SYSTEM_V2_DESIGN.md §2/§3。

职责分工（三层分离，解决旧 LayerManager 的"假单例/双 import/快照漂移"）：
- LayerSpec   : 声明式、不可变、无精灵的层描述（真值源 A：层长什么样）
- LayerBucket : 每层一个精灵容器（保留 pygame.sprite.Group）
- LayerStack  : 每 GameWorld 一个实例，是"当前世界有哪些层/顺序/可见性"的真值源 B；
                提供 subscribe 供 UI / hover 缓存 / 渲染器观察，不再各自快照。

Layer 系统只做"严格按层序"的确定性排序；复杂遮挡交给 Structure（见 Renderable）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, List, Optional, Protocol, Tuple

import pygame


class LayerKind(Enum):
    """层在绘制带里的角色。

    - TERRAIN : 纯地面/背景，垫底、不 y 排、不交互、永不遮挡
    - OBJECT   : 普通对象层（含树/屋顶/墙体等会遮挡的东西，复杂遮挡交给 Structure）
    - EFFECT   : 特效/覆盖层，叠在最上
    """
    TERRAIN = "terrain"
    OBJECT = "object"
    EFFECT = "effect"


# 变更通知类型：kind, layer_name(可能为 None，如全量/内容级变更)
ChangeKind = str
_on_change_cb = Callable[[ChangeKind, Optional[str]], None]


class UnknownLayerError(KeyError):
    """层栈中不存在该层名且没有兜底层可回落时抛出。"""

    def __init__(self, name: str, stack_names: Iterable[str]):
        self.name = name
        self.stack_names = list(stack_names)
        super().__init__(
            f"未知图层 '{name}'，可用层: {sorted(self.stack_names)}；"
            f"未配置 default 兜底层，禁止隐式建层。"
        )


@dataclass(frozen=True)
class LayerSpec:
    """声明式层描述。不可变；任何修改返回新副本。"""
    name: str
    order: int = 0
    floor: int = 0
    kind: LayerKind = LayerKind.OBJECT
    visible: bool = True
    opacity: float = 1.0
    y_sorted: bool = False      # OBJECT 层内普通精灵是否按世界 Y 排（便利项）
    physics: bool = False       # 该层对象默认是否入物理/空间网格
    synthetic: bool = False     # True = 特殊层(default/test)，见 DESIGN §4
    source: Optional[str] = None  # 溯源，如 "tmx:terrain"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LayerKind):
            object.__setattr__(self, "kind", LayerKind(self.kind))

    # ---- 不可变派生 ----
    def with_visible(self, v: bool) -> "LayerSpec":
        return dataclasses_replace(self, visible=v)

    def with_order(self, o: int) -> "LayerSpec":
        return dataclasses_replace(self, order=o)

    # ---- 从 TMX 图层构建（懒 import pytmx，仅导入该层时触发） ----
    @classmethod
    def from_tmx(cls, tmx_layer: Any, source_prefix: str = "tmx") -> "LayerSpec":
        """把 pytmx 的一个图层转成 LayerSpec。分类逻辑见 DESIGN §8。

        kind 推断优先级：层自定义属性 kind > 内置名称映射 > 默认 OBJECT。
        名称映射（仅当没有显式 kind 且名字能对上时使用）：
          terrain/ground/bg/background/floor*        -> TERRAIN
          其余（含 walls/objects/tree/roof/…）         -> OBJECT
        """
        props = getattr(tmx_layer, "properties", None) or {}

        name = getattr(tmx_layer, "name", None) or props.get("name", "")
        order = _to_int(props.get("order"))
        floor = _to_int(props.get("floor")) or 0

        kind = props.get("kind")
        if kind is not None:
            kind = LayerKind(str(kind).lower())
        else:
            kind = cls._infer_kind(name)

        visible = bool(getattr(tmx_layer, "visible", props.get("visible", True)))
        opacity = float(getattr(tmx_layer, "opacity", props.get("opacity", 1.0)) or 1.0)
        physics = _to_bool(props.get("physics", False))

        # TERRAIN 默认不 y 排；OBJECT 默认也不排，需要时由层属性开
        y_sorted = props.get("y_sorted", False)
        if y_sorted in (None, ""):
            y_sorted = kind is LayerKind.OBJECT
        else:
            y_sorted = _to_bool(y_sorted)

        return cls(
            name=name,
            order=order if order is not None else 0,
            floor=floor,
            kind=kind,
            visible=visible,
            opacity=opacity,
            y_sorted=y_sorted,
            physics=physics,
            source=f"{source_prefix}:{name}",
        )

    # 仅供 TERRAIN 命中判断的通用词
    _TERRAIN_HINTS = ("terrain", "ground", "floor", "bg", "background")

    @classmethod
    def _infer_kind(cls, name: str) -> LayerKind:
        low = (name or "").lower()
        if any(h in low for h in cls._TERRAIN_HINTS):
            return LayerKind.TERRAIN
        return LayerKind.OBJECT


def dataclasses_replace(spec: LayerSpec, **kw) -> LayerSpec:
    """frozen dataclass 的字段替换（返回新对象）。"""
    base = {f.name: getattr(spec, f.name) for f in spec.__dataclass_fields__.values()}
    base.update(kw)
    return LayerSpec(**base)


class LayerBucket:
    """运行时每层桶：一份 spec 快照 + 一个精灵容器（保留 pygame Group）。"""

    def __init__(self, spec: LayerSpec):
        self.spec = spec
        self.sprites: pygame.sprite.Group = pygame.sprite.Group()

    def __repr__(self) -> str:
        return f"LayerBucket(spec={self.spec.name!r}, sprites={len(self.sprites)})"

    # ---- 便捷别名：让旧消费方(layer.name/layer.order)少改动即可迁移 ----
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def order(self) -> int:
        return self.spec.order

    # ---- 委托给 Group 的便捷用法 ----
    def add(self, obj: pygame.sprite.Sprite) -> None:
        self.sprites.add(obj)

    def remove(self, obj: pygame.sprite.Sprite) -> None:
        self.sprites.remove(obj)

    def __iter__(self) -> Iterator[pygame.sprite.Sprite]:
        return iter(self.sprites)

    def __len__(self) -> int:
        return len(self.sprites)

    def __contains__(self, obj) -> bool:
        return obj in self.sprites

    def sprites_list(self) -> List[pygame.sprite.Sprite]:
        return self.sprites.sprites()


class LayerStack:
    """每 GameWorld 一个实例的层栈（真值源 B）。状态在实例上，非类属性。"""

    def __init__(self, specs: Iterable[LayerSpec] = ()) -> None:
        self._buckets: List[LayerBucket] = []   # 保持插入序；查询按 order 稳定排
        self._subscribers: List[_on_change_cb] = []
        for s in specs:
            self.add_layer(s)

    # ---------------- 层增删改（都会 notify） ----------------
    def add_layer(self, spec: LayerSpec) -> None:
        """新增/同名更新一层。同名更新保持其当前桶位置，仅换 spec。"""
        if not isinstance(spec, LayerSpec):
            raise TypeError("add_layer 需要 LayerSpec 实例")
        for b in self._buckets:
            if b.spec.name == spec.name:
                b.spec = spec
                self._notify("update", spec.name)
                return
        self._buckets.append(LayerBucket(spec))
        self._sort_by_order()
        self._notify("add", spec.name)

    def remove(self, name: str) -> None:
        for i, b in enumerate(self._buckets):
            if b.spec.name == name:
                self._buckets.pop(i)
                self._notify("remove", name)
                return
        raise KeyError(name)

    def reorder(self, name: str, order: int) -> None:
        for b in self._buckets:
            if b.spec.name == name:
                b.spec = dataclasses_replace(b.spec, order=order)
                break
        self._sort_by_order()
        self._notify("reorder", name)

    def set_visible(self, name: str, v: bool) -> None:
        for b in self._buckets:
            if b.spec.name == name:
                b.spec = dataclasses_replace(b.spec, visible=v)
                self._notify("visible", name)
                return
        raise KeyError(name)

    def _sort_by_order(self) -> None:
        # 稳定排序：同 order 保持加入先后；order 升序 = 底层在前
        self._buckets.sort(key=lambda b: b.spec.order)

    # ---------------- 查询（单一真值源顺序） ----------------
    def get(self, name: str) -> Optional[LayerBucket]:
        for b in self._buckets:
            if b.spec.name == name:
                return b
        return None

    def iter_visible(self) -> Iterator[LayerBucket]:
        """按 order 稳定序，仅返回可见层。渲染/UI 主迭代入口。"""
        for b in self._buckets:
            if b.spec.visible:
                yield b

    def names(self) -> List[str]:
        return [b.spec.name for b in self._buckets]

    def __getitem__(self, name: str) -> LayerBucket:
        b = self.get(name)
        if b is None:
            raise KeyError(name)
        return b

    def __iter__(self) -> Iterator[LayerBucket]:
        return iter(self._buckets)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self._buckets)

    # ---------------- 内容归属（对象入层/取层） ----------------
    def add(self, name: str, obj: pygame.sprite.Sprite) -> LayerBucket:
        """统一入层入口。

        兜底策略（DESIGN §4）：未知层名时——
        - 若层栈里有 default 层 -> 宽松回落 default（warning）；
        - 否则直接抛 UnknownLayerError，禁止隐式建层。
        """
        b = self.get(name)
        if b is None:
            fb = self.get("default")
            if fb is None:
                raise UnknownLayerError(name, self.names())
            import warnings
            warnings.warn(
                f"未知图层 '{name}'，已兜底到 default 层。", UserWarning, stacklevel=2)
            b = fb
        b.add(obj)
        self._notify("contents", b.spec.name)
        return b

    def remove_object(self, obj: pygame.sprite.Sprite) -> Optional[LayerBucket]:
        """把对象从其所在层移除，返回原层桶（不在任何层则返回 None）。"""
        for b in self._buckets:
            if obj in b.sprites:
                b.remove(obj)
                self._notify("contents", b.spec.name)
                return b
        return None

    def find_bucket_of(self, obj: pygame.sprite.Sprite) -> Optional[LayerBucket]:
        for b in self._buckets:
            if obj in b.sprites:
                return b
        return None

    def clear(self) -> None:
        """清空所有层（保留层定义，仅移除全部对象）。"""
        for b in self._buckets:
            b.sprites.empty()
        self._notify("contents", None)

    def clear_layers(self) -> None:
        """移除全部层定义。"""
        self._buckets.clear()
        self._notify("remove", None)

    # ---------------- 订阅 ----------------
    def subscribe(self, cb: _on_change_cb) -> None:
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: _on_change_cb) -> None:
        if cb in self._subscribers:
            self._subscribers.remove(cb)

    def _notify(self, kind: ChangeKind, name: Optional[str]) -> None:
        # 复制列表，允许回调中再改层
        for cb in list(self._subscribers):
            cb(kind, name)

    def __repr__(self) -> str:
        return f"LayerStack(layers={[b.spec.name for b in self._buckets]!r})"


# ---------------- 绘制契约（Structure 接入点） ----------------
class Renderable(Protocol):
    """对象侧绘制契约。
    普通 Sprite 隐式满足（视为单叶子）；Structure 等"组合对象"实现
    iter_sorted_leaf_sprites() 返回按内部深度排好的叶子精灵列表，从而在
    Layer 里作为一个整体参与排序、展开绘制（DESIGN §6/§7）。
    """

    def iter_sorted_leaf_sprites(self) -> Iterable[pygame.sprite.Sprite]:
        ...


def is_composite(obj: Any) -> bool:
    """对象是否是组合对象（需展开内部精灵绘制）。"""
    return hasattr(obj, "iter_sorted_leaf_sprites")


def leaf_sprites_of(obj: Any) -> List[pygame.sprite.Sprite]:
    """取对象待绘制的叶子精灵。普通 Sprite 返回 [obj]，组合对象展开。"""
    if is_composite(obj):
        return list(obj.iter_sorted_leaf_sprites())
    return [obj]


def is_occluder(obj: Any) -> bool:
    """对象是否是“自遮挡”结构（其叶子需要逐片参与全局深度排序）。

    这由 Structure 声明 is_occluder = True 表达（见 src/core/build_game/house.py）。
    遮挡结构的叶子不能当作一整块画 —— 否则无法把「在内部」的角色插到
    后墙与前墙之间。True 时由 depth_sorted_draw_units 逐片展开。
    """
    return bool(getattr(obj, "is_occluder", False))


# ---------------- 深度锚点：Y 轴排序到底取对象的哪个 Y ----------------
class DepthAnchor(Enum):
    """深度排序使用的 Y 锚点。

    - BOTTOM : 对象包围盒【下缘】（最底部的那个 Y）—— 引擎默认。
               角色/物体以“脚底”参与排序，站在同一地面线上时次序自然，
               高矮不同的物体不会因为“中心更高”而被误判为更靠后。
    - CENTER : 对象中心 Y（旧行为，保留作 A/B 对比用）。
    """
    CENTER = "center"
    BOTTOM = "bottom"


# 单一开关：引擎默认用「最底部 Y」。可在 settings.DEPTH_ANCHOR 覆盖，
# 也可运行时用 set_depth_anchor() 切换（用于对比两套排序观感）。
DEPTH_ANCHOR = "bottom"

# settings 只读一次（惰性，避免 import settings 时建 GL 上下文 / 循环导入）
_settings_read = False


def set_depth_anchor(anchor: Any) -> str:
    """运行时切换深度锚点：'bottom'（默认）或 'center'。返回生效值。

    显式调用视为最终决定：会同时标记 settings 已读，避免随后的惰性 settings 读取
    把这次选择覆盖掉。
    """
    global DEPTH_ANCHOR, _settings_read
    v = getattr(anchor, "value", anchor)
    v = str(v).strip().lower()
    if v not in ("bottom", "center"):
        raise ValueError(f"未知深度锚点 {anchor!r}（可选 'bottom' / 'center'）")
    DEPTH_ANCHOR = v
    _settings_read = True
    return DEPTH_ANCHOR


def get_depth_anchor() -> str:
    _refresh_anchor_from_settings()
    return DEPTH_ANCHOR


def _refresh_anchor_from_settings() -> None:
    """启动后首次取深度时，允许 settings.DEPTH_ANCHOR 覆盖模块默认（失败静默）。"""
    global _settings_read
    if _settings_read:
        return
    _settings_read = True
    try:
        import settings as _s
        v = getattr(_s, "DEPTH_ANCHOR", None)
        if v is not None:
            set_depth_anchor(v)
    except Exception:
        pass


def _center_y(obj: Any) -> float:
    """中心锚点深度：优先 y()，退回 centery。"""
    y = getattr(obj, "y", None)
    if callable(y):
        try:
            return float(y())
        except Exception:
            pass
    return float(getattr(obj, "centery", 0) or 0)


def _bottom_y(obj: Any) -> float:
    """底部锚点深度：对象【包围盒下缘】的世界 Y。

    解析优先级（逐级回落，保证任何对象都能给出一个数）：
      0) depth_base_y()          —— Structure 等组合对象自报“地面基准线”
                                    （多格墙/房子：整块共用一条基准线，而不是每格各自下缘）
      1) world_position.bottom   —— 世界坐标包围盒（叶子/结构/普通对象都有）
      2) centery + image.height/2 —— 有图无 world_position 时按图高推下缘
      3) rect.bottom             —— 最后手段（注意 rect 可能是屏幕坐标）
      4) centery                 —— 无几何信息时退回中心（等价旧行为）
    """
    base = getattr(obj, "depth_base_y", None)
    if callable(base):
        try:
            return float(base())
        except Exception:
            pass

    wp = getattr(obj, "world_position", None)
    if wp is not None:
        h = getattr(wp, "height", 0) or 0
        bottom = getattr(wp, "bottom", None)
        if bottom is not None and h:
            return float(bottom)

    cy = getattr(obj, "centery", None)
    if cy is not None:
        img = getattr(obj, "image", None)
        if img is not None:
            try:
                return float(cy) + float(img.get_height()) / 2.0
            except Exception:
                pass
        rect = getattr(obj, "rect", None)
        if rect is not None:
            try:
                return float(rect.bottom)
            except Exception:
                pass
        return float(cy)

    r = getattr(obj, "rect", None)
    if r is not None:
        return float(r.bottom)
    return 0.0


def depth_y(obj: Any) -> float:
    """取对象用于深度排序的 Y（世界坐标，y 向下＝深者后画）。

    默认锚点 = BOTTOM（对象最底部的那个 Y）。切换见 set_depth_anchor / settings.DEPTH_ANCHOR。
    """
    _refresh_anchor_from_settings()
    if DEPTH_ANCHOR == "center":
        return _center_y(obj)
    return _bottom_y(obj)


def depth_sorted_draw_units(objects: Iterable[Any]) -> List[Any]:
    """把一组动态对象排成“按深度绘制”的原子列表，供渲染器逐项画。

    返回列表里的每个元素是一个可绘制的原子：
      - 普通 Sprite / GameObject : 它自己（单一叶子）。
      - 非遮挡 Structure        : 它自己（由渲染器经 get_sprite_order 展开，原子）。
      - 遮挡 Structure(occluder): 它的每片叶子（逐片，可被内部对象插到它们之间）。

    排序键（世界 Y，深者后画）：统一走 :func:`depth_y`，即对象的【下缘 Y】
    （默认 DepthAnchor.BOTTOM；可切 DepthAnchor.CENTER 回到旧的 centery）：
      - 普通对象            : depth_y(obj)（默认 = 包围盒下缘）
      - 非遮挡 Structure    : depth_y(obj)（默认 = depth_base_y()，整块的地面基准线）
      - 遮挡结构的叶子      : structure.leaf_depth(leaf)（默认 = 该结构基准线 + layer_offset；
                              有 leaf_depth 时以它为准，房子按“部件地面线”分片）

    这样“在房子里”的角色/物体凭自身下缘自然落在后墙与前墙的基准线之间，
    无需手写 inside 判断 —— 遮挡正确性完全由几何深度决定。
    """
    units: List[Tuple[float, Any]] = []
    for obj in objects:
        if is_occluder(obj):
            off = float(getattr(obj, "layer_offset", 0) or 0)
            per_leaf = getattr(obj, "leaf_depth", None)
            for leaf in obj.iter_sorted_leaf_sprites():
                if callable(per_leaf):
                    key = float(per_leaf(leaf))
                else:
                    key = depth_y(leaf) + off
                units.append((key, leaf))
            continue
        # 普通对象 / 非遮挡结构：作为整体参与排序
        units.append((depth_y(obj), obj))
    units.sort(key=lambda t: t[0])
    return [u for _, u in units]


# ---------------- 遮挡交互：谁挡住了焦点实体 ----------------
#
# 排序只回答"谁更近"，不回答"谁真的挡住了谁" —— 两块精灵可以一个更近却完全
# 不相交，那它对画面毫无影响。遮挡交互补上这一层：把"更近 且 实际相交"的单位
# 挑出来，交给宿主结构声明的策略（淡出/剖视/描边）处理。
#
class OcclusionPolicy(Enum):
    """结构被判为"挡住焦点实体"时该怎么办（策略声明，效果可后置）。"""

    NONE = "none"          # 不管（默认）
    FADE = "fade"          # 淡出
    CUTAWAY = "cutaway"    # 剖视：直接不画（如屋子前墙让路）
    OUTLINE = "outline"    # 保留但描边提示


def world_rect_of(unit: Any) -> Optional[pygame.Rect]:
    """取一个绘制原子的【判定矩形】（world_position，即引擎认为它占多大）。"""
    wp = getattr(unit, "world_position", None)
    if wp is not None:
        w = getattr(wp, "width", 0) or 0
        h = getattr(wp, "height", 0) or 0
        if w and h:
            return pygame.Rect(int(wp.left), int(wp.top), int(w), int(h))
    r = getattr(unit, "rect", None)
    if r is not None and getattr(r, "width", 0) and getattr(r, "height", 0):
        return pygame.Rect(int(r.left), int(r.top), int(r.width), int(r.height))
    return None


def art_rect_of(unit: Any) -> Optional[pygame.Rect]:
    """取一个绘制原子【实际画出来】的世界矩形（非透明像素的包围盒）。

    为什么不能直接用 world_position：墙的一格 surface 是 64×64，但贴图往往
    只在半个格子里有像素（另一半全透明 —— 见 tests/probe_wall_art.py 实测：
    竖墙每格 art 宽 32px、判定宽 64px）。判定遮挡时若按 64px 算，就会把
    "其实根本没盖住"的情况误报成遮挡。

    结果缓存在 sprite 上，键含 image 对象本身 —— 墙体换贴图（image 变了）
    或移动（world_position 变了）会自动失效，不会读到旧值。
    """
    wp = getattr(unit, "world_position", None)
    img = getattr(unit, "image", None)
    if wp is None or img is None:
        return world_rect_of(unit)

    wsp = (int(wp.left), int(wp.top), int(wp.width or 0), int(wp.height or 0))
    cached = getattr(unit, "_art_rect_cache", None)
    if cached is not None and cached[0] is img and cached[1] == wsp:
        return cached[2]

    try:
        iw, ih = img.get_width(), img.get_height()
        br = img.get_bounding_rect()
    except Exception:
        return world_rect_of(unit)
    if iw <= 0 or ih <= 0 or br.width <= 0 or br.height <= 0:
        return world_rect_of(unit)

    # 贴图可能被整体缩放（world_position 尺寸 != 图像像素尺寸），按比例映射
    sx = float(wsp[2]) / float(iw) if wsp[2] else 1.0
    sy = float(wsp[3]) / float(ih) if wsp[3] else 1.0
    r = pygame.Rect(int(wsp[0] + br.left * sx), int(wsp[1] + br.top * sy),
                    max(1, int(round(br.width * sx))), max(1, int(round(br.height * sy))))
    try:
        unit._art_rect_cache = (img, wsp, r)
    except Exception:
        pass
    return r


def occluders_of(units: List[Any], focus: Any, *, owner_of=None) -> List[Tuple[Any, Any, "OcclusionPolicy"]]:
    """在一份【已按深度排好序】的绘制单位列表里，找出真正挡住 focus 的单位。

    两个条件必须同时成立：
      1) 深度更近 —— unit 排在 focus 之后（后画 => 盖在它上面）；
      2) 实际相交 —— 两者的【实际绘制范围】(art_rect_of) 有重叠。
         几何上"更近"不等于"挡住了"：两块完全不相交的精灵谁先谁后都看不出差别。

    用 art_rect 而不是 world_position 是刻意的：墙的判定框有 64px 而可见贴图
    只有 32px，按判定框算会大量误报。

    owner_of(unit) 用来把一个绘制原子追溯回它的宿主结构（遮挡结构逐片展开后，
    叶子本身已经不带宿主信息，需要调用方给这个映射，见 owner_index_of）。

    返回 [(unit, owner, policy), ...]，按绘制顺序排列（挡在最前面的排在最后）。
    """
    fr = art_rect_of(focus)
    if fr is None:
        return []
    at = next((i for i, u in enumerate(units) if u is focus), None)
    if at is None:
        return []

    out: List[Tuple[Any, Any, OcclusionPolicy]] = []
    for unit in units[at + 1:]:
        ur = art_rect_of(unit)
        if ur is None or not ur.colliderect(fr):
            continue
        owner = owner_of(unit) if callable(owner_of) else None
        out.append((unit, owner, occlusion_policy_of(unit, owner)))
    return out


def _as_policy(v: Any) -> "OcclusionPolicy":
    if isinstance(v, OcclusionPolicy):
        return v
    try:
        return OcclusionPolicy(str(v).strip().lower())
    except Exception:
        return OcclusionPolicy.NONE


def occlusion_policy_of(unit: Any, owner: Any = None) -> "OcclusionPolicy":
    """取某个绘制单位的遮挡策略。

    优先问宿主的逐叶策略 occlusion_policy_for(leaf)（同一结构的不同叶子可以有
    不同策略，例如房子的前墙剖视、后墙不动），取不到再退回结构的整体声明。
    """
    target = owner if owner is not None else unit
    fn = getattr(target, "occlusion_policy_for", None)
    if callable(fn):
        try:
            return _as_policy(fn(unit))
        except Exception:
            pass
    return _as_policy(getattr(target, "occlusion_policy", OcclusionPolicy.NONE))


def owner_index_of(objects: Iterable[Any]) -> dict:
    """建 "叶子 id -> 淡出单位" 索引。

    淡出单位由宿主结构自己回答（Structure.occlusion_owner_of）：
      - 普通结构        -> 它自己（整块一起淡）
      - 组合/自遮挡结构 -> 该叶子所属的【部件】（House 的四面墙各自独立淡）
    遮挡结构逐片展开后，叶子本身不带这个信息，所以要靠这张表追溯。
    """
    m = {}
    for obj in objects:
        if is_occluder(obj):
            ask = getattr(obj, "occlusion_owner_of", None)
            for leaf in obj.iter_sorted_leaf_sprites():
                if callable(ask):
                    try:
                        m[id(leaf)] = ask(leaf)
                        continue
                    except Exception:
                        pass
                m[id(leaf)] = obj
    return m


def occlusion_report(objects: Iterable[Any], focus: Any):
    """一次跑完：排序 -> 找遮挡者。返回 (units, occluders)。"""
    objs = list(objects)
    units = depth_sorted_draw_units(objs)
    owners = owner_index_of(objs)
    return units, occluders_of(units, focus, owner_of=lambda u: owners.get(id(u)))


class OcclusionFader:
    """遮挡淡入淡出：把"谁挡住了焦点"翻译成"每个单位这一帧该多透明"。

    为什么以【宿主结构】为单位缓动，而不是以叶子：一面墙的叶子必须一起淡。
    否则同一面墙会有的叶子淡、有的不淡，看起来像墙裂开了。

    状态机（每个 key 一个当前 alpha）：
      - 进入遮挡者名单 -> 目标 = target（通常 0.35），从当前值按 speed 每秒趋近
      - 离开名单       -> 目标 = 1.0，同样趋近
      - 完全恢复到 1.0 -> 从表里删掉（字典不随时间涨）
    新出现的遮挡者从 1.0 起步，所以是"淡出"而不是"啪一下变透明"。
    """

    def __init__(self, *, target: float = 0.35, speed: float = 5.0, enabled: bool = True):
        self.target = max(0.0, min(1.0, float(target)))
        self.speed = max(0.0, float(speed))
        self.enabled = bool(enabled)
        self._cur: dict = {}

    @staticmethod
    def key_of(unit: Any, owner: Any = None) -> int:
        """淡出的粒度键：有宿主就用宿主（整面墙一起淡），没有就单位自己。"""
        return id(owner) if owner is not None else id(unit)

    def update(self, occluders, dt: float) -> dict:
        """喂本帧的遮挡者列表 [(unit, owner, policy), ...]，返回 {key: alpha}。"""
        if not self.enabled:
            if self._cur:
                self._cur.clear()
            return self._cur

        active = set()
        for item in occluders or ():
            unit, owner, policy = item
            if policy is OcclusionPolicy.NONE:
                continue                      # NONE = 不参与淡出
            active.add(self.key_of(unit, owner))

        for k in active:                      # 新遮挡者从 1.0 起步
            self._cur.setdefault(k, 1.0)

        step = self.speed * max(0.0, float(dt))
        for k in list(self._cur):
            goal = self.target if k in active else 1.0
            cur = self._cur[k]
            cur = min(goal, cur + step) if cur < goal else max(goal, cur - step)
            if k not in active and cur >= 1.0:
                del self._cur[k]              # 恢复正常就忘掉
            else:
                self._cur[k] = cur
        return self._cur

    def alpha_of(self, unit: Any, owner: Any = None) -> float:
        return self._cur.get(self.key_of(unit, owner), 1.0)

    def clear(self) -> None:
        self._cur.clear()


def occlusion_frame(objects: Iterable[Any], focus: Any):
    """一次算好渲染需要的三样：绘制顺序 units、宿主索引 owners、遮挡者 occluders。

    宿主索引是为了让"逐片展开"的遮挡结构（House 的叶子）还能追溯回它的宿主，
    这样淡出才能按整面墙统一，而不是按叶子各淡各的。
    """
    objs = list(objects)
    units = depth_sorted_draw_units(objs)
    owners = owner_index_of(objs)
    occ = occluders_of(units, focus, owner_of=lambda u: owners.get(id(u)))
    return units, owners, occ


def unit_alpha(unit: Any, occluders=None, *, fader: "OcclusionFader" = None,
               owner: Any = None, now: Any = None) -> float:
    """取某个绘制单位这一帧的不透明度。

    传了 fader（真实淡入淡出）就用它；没传就是纯占位 1.0 —— 无状态、画面零变化。
    """
    if fader is None:
        return 1.0
    return fader.alpha_of(unit, owner)


# ---------------- 装配：从 settings 建立特殊层（default/test） ----------------
def configured_special_specs() -> List[LayerSpec]:
    """按 settings.LAYER_DEFAULTS / ENABLE_* 建立特殊层 spec（default 与 test 独立）。

    default 与 test 各自独立、可分别开关。惰性 import settings 以避免循环导入。
    返回的 specs 顺序无关（LayerStack 会按 order 稳定排）。
    """
    import settings as _settings

    specs: List[LayerSpec] = []
    enabled = {
        "default": bool(getattr(_settings, "ENABLE_DEFAULT_LAYER", True)),
        "test": bool(getattr(_settings, "ENABLE_TEST_LAYER", True)),
    }
    cfg = dict(getattr(_settings, "LAYER_DEFAULTS", {}))

    # 先放 test、再放 default，保证 default 的 order 若相同则插在 test 之前（更底层兜底）。
    for name in ("test", "default"):
        opts = dict(cfg.get(name, {}) or {})
        if not enabled.get(name, False):
            continue
        kind = opts.pop("kind", "object")
        opts.pop("synthetic", None)   # 特殊层恒为 synthetic，避免与显式实参冲突
        specs.append(
            LayerSpec(name=name, kind=LayerKind(kind),
                      synthetic=True,
                      order=int(opts.pop("order", 1000)),
                      floor=int(opts.pop("floor", 0)),
                      visible=bool(opts.pop("visible", True)),
                      opacity=float(opts.pop("opacity", 1.0)),
                      y_sorted=bool(opts.pop("y_sorted", False)),
                      physics=bool(opts.pop("physics", False)),
                      **opts)
        )
    return specs


# ---------------- 内部小工具 ----------------
def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
