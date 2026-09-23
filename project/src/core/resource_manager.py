import pygame
import os
import hashlib
from typing import Optional, Tuple, List, Dict, Any, Union, Callable
from pathlib import Path
from collections import defaultdict

from settings import PROJECT_ROOT
from src.core.command import Command

# ============================================================
# 1. 全新的内部数据结构（彻底解耦）
# ============================================================

# 物理缓存：存储最终加载并应用了缩放/翻转/透明键的 Surface 列表
# Key = (相对路径, 缩放元组, 水平翻转, 垂直翻转, 透明键, 分割器标识)
# Value = List[pygame.Surface]
_phys_cache: Dict[tuple, List[pygame.Surface]] = {}

# 逻辑ID映射：将人类可读的 ID 指向物理缓存中的具体某一帧
# Key = 逻辑ID (例如 "dungeon/wall_0")
# Value = (phys_key, frame_index)   # frame_index = -1 表示整个列表（兼容旧Tileset）
_logical_map: Dict[str, tuple] = {}

# 反向查找：Surface对象id -> 逻辑ID（用于 get_id_from_surface 等兼容接口）
_surf_to_logical_id: Dict[int, str] = {}

# 程序化生成专用缓存（逻辑ID依然用 _logical_map 指向这里）
_procedural_phys_cache: Dict[tuple, pygame.Surface] = {}

# 分割器注册表（用于生成稳定的缓存键，避免不同分割器打架）
_splitter_registry: Dict[int, str] = {}
_splitter_counter = 0

# ------------------------------------------------------------
# 图像「来源登记表」（给 serializer 用）
# ------------------------------------------------------------
# 目的：让序列化器知道一张 Surface 是【怎么来的】，从而只保存
# 「路径 + 切分方式 + 变换 + 帧号」，而不是把整张图的像素写进存档。
#
# Key   = id(surface)
# Value = (surface 强引用, 来源信息 dict)
#   · 强引用是必须的：Surface 被 GC 后 id 会被复用，会导致来源张冠李戴。
#   · 来源信息 dict 的 schema（全部可 JSON 化，内部字段以 _ 开头，序列化器会丢弃）：
#       {'kind': 'load',       'path': 'a/b.png', 'split': {...}, 'index': 0,
#        'scale': (64,64) | None, 'flip_x': False, 'flip_y': False,
#        'colorkey': (r,g,b) | None, '_key': phys_key}
#       {'kind': 'procedural', 'resource_type': 'portal', 'params': (...) }
#       {'kind': 'crop',       'parent': <来源信息>, 'rect': (x, y, w, h)}
_surf_to_source: Dict[int, tuple] = {}

# 登记表版本号：序列化器用它判断自己的「像素哈希 -> 来源」索引是否过期
_source_registry_version = 0


def get_source_registry_version() -> int:
    """返回来源登记表版本号（任何一次登记/清空都会 +1）"""
    return _source_registry_version


def _register_surface_source(surf: 'pygame.Surface', info: dict) -> None:
    """内部：登记一张 Surface 的来源（同 id 覆盖，并让版本号前进）"""
    global _source_registry_version
    if surf is None or not isinstance(surf, pygame.Surface):
        return
    _surf_to_source[id(surf)] = (surf, dict(info))
    _source_registry_version += 1


# ============================================================
# 2. 路径规范化工具（强制从 assets 文件夹往下找）
# ============================================================

def _normalize_path(path: str) -> str:
    """将任意路径转换为相对于 assets 文件夹的 POSIX 风格路径"""
    p = Path(path)
    assets_root = Path(PROJECT_ROOT) / "assets"

    # 如果已经是绝对路径，尝试转为相对
    if p.is_absolute():
        try:
            rel = p.relative_to(assets_root)
            return str(rel).replace('\\', '/')
        except ValueError:
            # 如果不在 assets 下，原样返回（但这种情况应避免）
            return str(p).replace('\\', '/')

    # 如果是相对路径，去掉可能的 assets/ 前缀
    parts = p.parts
    if parts and parts[0] == 'assets':
        p = Path(*parts[1:])

    return str(p).replace('\\', '/')


def get_resource_path(relative_path: str) -> str:
    """外部可用的路径解析接口（保持不变）"""
    base = Path(PROJECT_ROOT) / "assets"
    if not base.exists():
        raise FileNotFoundError(f"找不到 assets 目录: {base}")
    return str(base / relative_path)


# ============================================================
# 3. 分割器工具（内置 + 注册机制）
# ============================================================

# 分割器类型：接收一个 Surface，返回 List[Surface]
Splitter = Callable[[pygame.Surface], List[pygame.Surface]]


def _get_splitter_id(splitter: Splitter) -> str:
    """为分割器生成稳定的字符串标识，用于缓存键"""
    if hasattr(splitter, '__name__'):
        return splitter.__name__
    # 匿名函数用 id 注册
    global _splitter_counter
    sid = id(splitter)
    if sid not in _splitter_registry:
        _splitter_registry[sid] = f"anon_splitter_{_splitter_counter}"
        _splitter_counter += 1
    return _splitter_registry[sid]


# ---------- 内置分割器 ----------
# 每个分割器都带一个可序列化的 splitter_spec，序列化器据此重建同一套切分。
# 同时给 __name__ 加上区分度：否则 split_grid(8,8) 与 split_grid(9,6) 都叫 "_split"，
# 缓存键会撞在一起（不同切分被当成同一资源）。

def split_horizontal(frame_count: int) -> Splitter:
    def _split(surf: pygame.Surface) -> List[pygame.Surface]:
        w = surf.get_width() // frame_count
        if w * frame_count != surf.get_width():
            raise ValueError(f"宽度 {surf.get_width()} 无法均分为 {frame_count} 份")
        return [surf.subsurface(pygame.Rect(i * w, 0, w, surf.get_height()))
                for i in range(frame_count)]

    _split.__name__ = f"horizontal_{frame_count}"
    _split.splitter_spec = {'mode': 'horizontal', 'count': int(frame_count)}
    return _split


def split_vertical(frame_count: int) -> Splitter:
    def _split(surf: pygame.Surface) -> List[pygame.Surface]:
        h = surf.get_height() // frame_count
        if h * frame_count != surf.get_height():
            raise ValueError(f"高度 {surf.get_height()} 无法均分为 {frame_count} 份")
        return [surf.subsurface(pygame.Rect(0, i * h, surf.get_width(), h))
                for i in range(frame_count)]

    _split.__name__ = f"vertical_{frame_count}"
    _split.splitter_spec = {'mode': 'vertical', 'count': int(frame_count)}
    return _split


def split_grid(cols: int, rows: int) -> Splitter:
    def _split(surf: pygame.Surface) -> List[pygame.Surface]:
        w = surf.get_width() // cols
        h = surf.get_height() // rows
        if w * cols != surf.get_width() or h * rows != surf.get_height():
            raise ValueError(f"尺寸 {surf.get_size()} 无法均分为 {cols}x{rows}")
        return [surf.subsurface(pygame.Rect(c * w, r * h, w, h))
                for r in range(rows) for c in range(cols)]

    _split.__name__ = f"grid_{cols}x{rows}"
    _split.splitter_spec = {'mode': 'grid', 'cols': int(cols), 'rows': int(rows)}
    return _split


def split_custom(callback: Callable[[pygame.Surface], List[pygame.Rect]]) -> Splitter:
    """终极灵活：传回调返回 Rect 列表（回调无法序列化，因此无法按路径重建）"""

    def _split(surf: pygame.Surface) -> List[pygame.Surface]:
        return [surf.subsurface(rect) for rect in callback(surf)]

    # 回调不可序列化 -> 明确标记为"来源不可重建"，序列化器会退回像素保存
    _split.__name__ = f"custom_{id(callback)}"
    _split.splitter_spec = None
    return _split


def get_splitter_spec(splitter: Optional[Splitter]) -> Optional[dict]:
    """取出分割器的可序列化描述；返回 None 表示该分割器无法按描述重建"""
    spec = getattr(splitter, 'splitter_spec', None)
    return dict(spec) if isinstance(spec, dict) else None


def make_splitter_from_spec(spec: Optional[dict]) -> Optional[Splitter]:
    """按下述描述重建分割器（与 get_splitter_spec 互逆）

    {'mode': 'horizontal', 'count': n}
    {'mode': 'vertical',   'count': n}
    {'mode': 'grid',       'cols': c, 'rows': r}
    """
    if not isinstance(spec, dict):
        return None
    mode = spec.get('mode')
    try:
        if mode == 'horizontal':
            return split_horizontal(int(spec['count']))
        if mode == 'vertical':
            return split_vertical(int(spec['count']))
        if mode == 'grid':
            return split_grid(int(spec['cols']), int(spec['rows']))
    except (KeyError, TypeError, ValueError):
        return None
    return None


# ============================================================
# 4. 核心加载与注册函数（内部）
# ============================================================

def _load_raw_image(path: str) -> pygame.Surface:
    """加载原始图片（带透明通道）"""
    abs_path = path if os.path.isabs(path) else get_resource_path(path)
    try:
        return pygame.image.load(abs_path).convert_alpha()
    except (FileNotFoundError, pygame.error) as e:
        raise FileNotFoundError(f"[ResourceManager] 加载失败: {abs_path}")


def _apply_transforms_to_frames(
        frames: List[pygame.Surface],
        scale: Optional[Tuple[int, int]],
        flip_x: bool,
        flip_y: bool,
        colorkey: Optional[Tuple[int, int, int]]
) -> List[pygame.Surface]:
    """对帧列表统一应用变换，返回新列表"""
    result = []
    for f in frames:
        if colorkey:
            f.set_colorkey(colorkey)
        if flip_x or flip_y:
            f = pygame.transform.flip(f, flip_x, flip_y)
        if scale:
            f = pygame.transform.scale(f, scale)
        result.append(f)
    return result


def _make_phys_key(
        rel_path: str,
        splitter_id: str,
        scale: Optional[Tuple[int, int]],
        flip_x: bool,
        flip_y: bool,
        colorkey: Optional[Tuple[int, int, int]]
) -> tuple:
    """生成物理缓存键"""
    return (rel_path, splitter_id, scale, flip_x, flip_y, colorkey)


def _register_logical_ids(
        base_stem: str,  # 例如 "dungeon/wall"
        frames: List[pygame.Surface],
        phys_key: tuple
):
    """将帧列表注册到逻辑映射表，生成 ID: base_stem_0, base_stem_1, ..."""
    # 注册整个列表（兼容旧 Tileset 返回整体）
    list_id = base_stem
    _logical_map[list_id] = (phys_key, -1)

    # 注册每一帧
    for idx, surf in enumerate(frames):
        frame_id = f"{base_stem}_{idx}"
        _logical_map[frame_id] = (phys_key, idx)
        _surf_to_logical_id[id(surf)] = frame_id


def _stem_from_rel_path(rel_path: str) -> str:
    """assets 内相对路径 -> 逻辑ID前缀（去掉扩展名，保留子目录）"""
    base_stem = Path(rel_path).stem
    parent_dir = str(Path(rel_path).parent.as_posix())
    if parent_dir == '.':
        return base_stem
    return f"{parent_dir}/{base_stem}"


def _register_load_sources(
        rel_path: str,
        splitter: Splitter,
        scale: Optional[Tuple[int, int]],
        flip_x: bool,
        flip_y: bool,
        colorkey: Optional[Tuple[int, int, int]],
        frames: List[pygame.Surface],
        phys_key: tuple
) -> None:
    """把一批"从文件加载出来的帧"登记成可重建的来源信息"""
    spec = get_splitter_spec(splitter)
    for idx, surf in enumerate(frames):
        _register_surface_source(surf, {
            'kind': 'load',
            'path': rel_path,
            'split': dict(spec) if spec else None,
            'index': idx,
            'scale': tuple(scale) if scale else None,
            'flip_x': bool(flip_x),
            'flip_y': bool(flip_y),
            'colorkey': tuple(colorkey) if colorkey else None,
            '_key': phys_key,
        })


def _load_and_register(
        path: str,
        splitter: Splitter,
        scale: Optional[Tuple[int, int]] = None,
        flip_x: bool = False,
        flip_y: bool = False,
        colorkey: Optional[Tuple[int, int, int]] = None
) -> List[pygame.Surface]:
    """
    底层通用加载：读取文件 → 分割器切分 → 应用变换 → 缓存 → 注册ID + 注册来源
    """
    rel_path = _normalize_path(path)
    splitter_id = _get_splitter_id(splitter)
    phys_key = _make_phys_key(rel_path, splitter_id, scale, flip_x, flip_y, colorkey)

    # 1. 检查物理缓存
    if phys_key in _phys_cache:
        frames = _phys_cache[phys_key]
        # 缓存命中通常什么都不用做；但若登记曾丢失（例如手工清过逻辑映射），
        # 必须补登记，否则序列化器会退化成"保存全部像素"。
        # 只查第一帧即可：登记表持有强引用，已登记的 Surface id 不会被复用。
        if not frames or id(frames[0]) not in _surf_to_source:
            full_stem = _stem_from_rel_path(rel_path)
            _register_logical_ids(full_stem, frames, phys_key)
            _register_load_sources(rel_path, splitter, scale, flip_x, flip_y, colorkey, frames, phys_key)
        return frames

    # 2. 读取原始图片
    raw = _load_raw_image(path)

    # 3. 分割器切割（得到原始帧，未变换）
    raw_frames = splitter(raw)

    # 4. 应用变换（缩放/翻转/透明键）
    transformed_frames = _apply_transforms_to_frames(
        raw_frames, scale, flip_x, flip_y, colorkey
    )

    # 5. 存入物理缓存
    _phys_cache[phys_key] = transformed_frames

    # 6. 注册逻辑ID
    # 基础路径：去掉扩展名，保留 assets 内的子文件夹结构
    full_stem = _stem_from_rel_path(rel_path)  # 例如 "dungeon/wall"

    _register_logical_ids(full_stem, transformed_frames, phys_key)

    # 7. 注册来源（让 serializer 可以只保存路径）
    _register_load_sources(rel_path, splitter, scale, flip_x, flip_y, colorkey,
                           transformed_frames, phys_key)

    return transformed_frames


# ============================================================
# 5. 公开 API（保持签名完全不变）
# ============================================================

def load_image(
        path: str,
        scale: Optional[Tuple[int, int]] = None,
        frame_count: int = 1,
        colorkey: Optional[Tuple[int, int, int]] = None,
        flip_x: bool = False,
        flip_y: bool = False
) -> List[pygame.Surface]:
    """
    加载图像（水平切割），返回帧列表。
    签名与旧版完全一致。
    """
    splitter = split_horizontal(frame_count)
    return _load_and_register(path, splitter, scale, flip_x, flip_y, colorkey)


def load_tileset(
        path: str,
        frame_count: int = 1,
        scale: Optional[Tuple[int, int]] = None,
        colorkey: Optional[Tuple[int, int, int]] = None,
        flip_x: bool = False,
        flip_y: bool = False,
        splitter: Optional[Splitter] = None  # ← 新增可选参数，不影响旧调用
):
    """
    加载精灵表，返回 Tileset 对象。
    如果传入 splitter，则使用自定义分割器；否则使用默认水平切割。
    """
    if splitter is None:
        splitter = split_horizontal(frame_count)

    frames = _load_and_register(path, splitter, scale, flip_x, flip_y, colorkey)

    # 获取主ID（兼容旧 Tileset 行为）
    main_id = _stem_from_rel_path(_normalize_path(path))

    # 构建 Tileset 对象（保持与原接口一致）
    class Tileset:
        __slots__ = ('frames', 'id')

        def __init__(self, frames, id):
            self.frames = frames
            self.id = id

        def get_frame(self, index):
            if 0 <= index < len(self.frames):
                return self.frames[index]
            raise IndexError

        def __len__(self):
            return len(self.frames)

        def __getitem__(self, index):
            return self.get_frame(index)

        def __iter__(self):
            return iter(self.frames)

    return Tileset(frames, main_id)


def load_procedural_surface(resource_type: str, params: tuple) -> pygame.Surface:
    """
    加载程序化 Surface，签名完全不变。
    内部使用独立的程序化缓存，但逻辑ID仍注册到 _logical_map。
    """
    key = (resource_type, params)

    # 检查程序化物理缓存
    if key in _procedural_phys_cache:
        return _procedural_phys_cache[key]

    # 生成 Surface（原绘图函数保持不变）
    if resource_type == 'wall_circle':
        center, radius = params
        surf = _create_circle_surface(center, radius)
    elif resource_type == 'wall_rect':
        pos, size = params
        surf = _create_rect_surface(pos, size)
    elif resource_type == 'wall_polygon':
        points = params
        surf = _create_polygon_surface(points)
    elif resource_type == 'portal':
        surf = _create_portal_surface()
    elif resource_type == "placeholder":
        surf = _create_placeholder_surface()
    else:
        raise ValueError(f"Unknown procedural resource type: {resource_type}")

    # 存入程序化缓存
    _procedural_phys_cache[key] = surf

    # 注册到逻辑映射（用 resource_type + 参数哈希作为逻辑ID）
    import hashlib
    param_str = str(params).encode()
    param_hash = hashlib.md5(param_str).hexdigest()[:8]
    logical_id = f"__procedural__/{resource_type}_{param_hash}"
    _logical_map[logical_id] = (key, -1)  # -1 表示整体
    _surf_to_logical_id[id(surf)] = logical_id
    # 登记来源：序列化器只需保存 (resource_type, params) 就能重建，无需保存像素
    _register_surface_source(surf, {
        'kind': 'procedural',
        'resource_type': resource_type,
        'params': params,
        '_key': key,
    })

    return surf


# ============================================================
# 6. ID 查询与资源获取接口（保持完全兼容）
# ============================================================

def get_surface_by_id(id_str: str) -> Optional[Union[pygame.Surface, List[pygame.Surface]]]:
    """通过逻辑ID获取资源（可能是单帧 Surface 或帧列表）"""
    entry = _logical_map.get(id_str)
    if entry is None:
        return None

    phys_key, frame_index = entry

    # 尝试从物理缓存中取
    frames = _phys_cache.get(phys_key)
    if frames is None:
        # 也可能是程序化资源
        proc_surf = _procedural_phys_cache.get(phys_key)
        if proc_surf is not None:
            return proc_surf
        return None

    if frame_index == -1:
        return frames
    if 0 <= frame_index < len(frames):
        return frames[frame_index]
    return None


def get_id_from_surface(surf: pygame.Surface) -> Optional[str]:
    """通过 Surface 获取其逻辑ID（兼容旧接口）"""
    return _surf_to_logical_id.get(id(surf))


def get_frame_from_tileset(tileset_id: str, index: int) -> Optional[pygame.Surface]:
    """通过 tileset 主ID和索引获取单帧"""
    frame_id = f"{tileset_id}_{index}"
    return get_surface_by_id(frame_id)


def get_tileset_frames(tileset_id: str) -> Optional[List[pygame.Surface]]:
    """通过 tileset 主ID获取整个帧列表"""
    resource = get_surface_by_id(tileset_id)
    if isinstance(resource, list):
        return resource
    return None


def register_existing_surface(surf: pygame.Surface, custom_id: Optional[str] = None,
                              source: Optional[dict] = None) -> str:
    """
    手动注册外部创建的 Surface（兼容接口）。
    如果不提供 custom_id，自动生成一个。
    source: 可选。传入来源信息（见 make_load_source_info / get_surface_source_info），
            登记后这张 Surface 在存档里就只占一行路径而不是整块像素。
    """
    # 如果已注册，直接返回
    existing = _surf_to_logical_id.get(id(surf))
    if existing:
        if source is not None:
            _register_surface_source(surf, source)
        return existing

    if custom_id is None:
        import hashlib, time
        # 用时间戳+id生成一个唯一标识
        raw = f"external_{time.time()}_{id(surf)}".encode()
        custom_id = hashlib.md5(raw).hexdigest()[:16]

    # 构造一个虚拟物理键，仅用于占位
    dummy_key = ("__external__", custom_id)
    _logical_map[custom_id] = (dummy_key, -1)
    _surf_to_logical_id[id(surf)] = custom_id
    # 注意：物理缓存不存外部 Surface，仅保留逻辑映射
    if source is not None:
        _register_surface_source(surf, source)
    return custom_id


# ============================================================
# 7. 其他兼容接口
# ============================================================

def _resolve_derived_source(surf: 'pygame.Surface') -> Optional[dict]:
    """处理 subsurface 裁剪出来的 Surface：沿父链往上找已登记的血缘。

    例：House.png 的一帧 frame → frame.subsurface(rect1) → .subsurface(rect2)
    会被还原成 {'kind': 'crop', 'parent': <frame 的来源>, 'rect': ...} 的嵌套描述。
    """
    chain = []  # [(相对直接父面的偏移, 尺寸)]，从最外层子面开始收集
    cur = surf
    info = None
    for _ in range(32):  # 深度上限，防御异常实现导致的死循环
        try:
            parent = cur.get_parent()
        except Exception:
            return None
        if parent is None or parent is cur or not isinstance(parent, pygame.Surface):
            return None

        try:
            offset = cur.get_offset()
            size = cur.get_size()
        except Exception:
            return None
        chain.append((offset, size))

        entry = _surf_to_source.get(id(parent))
        if entry is not None and entry[0] is parent:
            info = dict(entry[1])
            break
        cur = parent
    if info is None:
        return None

    # 从最靠近根的一层开始，逐层向外套 crop（chain 是外层 -> 内层，故反转）
    for offset, size in reversed(chain):
        info = {
            'kind': 'crop',
            'parent': info,
            'rect': (int(offset[0]), int(offset[1]), int(size[0]), int(size[1])),
        }
    return info


def make_crop_source_info(parent_info: dict, rect) -> dict:
    """构造"从父图裁一块"的来源信息"""
    return {
        'kind': 'crop',
        'parent': dict(parent_info),
        'rect': (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
    }


def make_load_source_info(
        path: str,
        index: int = 0,
        scale: Optional[Tuple[int, int]] = None,
        flip_x: bool = False,
        flip_y: bool = False,
        colorkey: Optional[Tuple[int, int, int]] = None,
        frame_count: int = 1,
) -> dict:
    """手工构造"从文件加载"的来源信息（配合 register_surface_source 使用）"""
    return {
        'kind': 'load',
        'path': _normalize_path(path),
        'split': {'mode': 'horizontal', 'count': int(frame_count)},
        'index': int(index),
        'scale': tuple(scale) if scale else None,
        'flip_x': bool(flip_x),
        'flip_y': bool(flip_y),
        'colorkey': tuple(colorkey) if colorkey else None,
    }


# ------------------------------------------------------------
# 派生配方：flip / scale / rotate / compose 出来的图也要有"来源"
# ------------------------------------------------------------
# 场景：竖墙的默认纹理是 pygame.transform.flip 出来的，带侧脸的墙又是在
# base.copy() 上 blit 拼出来的。这些 Surface 既不是加载来的、内容也和原图不同，
# 像素哈希匹配不上 —— 不做处理就会整块写进存档。
#
# 做法：登记一条"配方"（builder 名 + 参数 + 父图来源），读档时按配方重新算一遍。
# 配方只描述"怎么算"，不含对象引用，天然不成环。

# name -> builder(spec: dict, parents: list[Surface]) -> Surface
_SOURCE_BUILDERS: Dict[str, Callable[[dict, list], 'pygame.Surface']] = {}


def register_surface_builder(name: str, builder: Callable[[dict, list], 'pygame.Surface']) -> None:
    """注册一个派生配方构造器（供自定义变换接入"只存配方"体系）"""
    _SOURCE_BUILDERS[str(name)] = builder


def get_surface_builder(name: str):
    return _SOURCE_BUILDERS.get(str(name))


def make_recipe_source_info(builder: str, spec: Optional[dict] = None,
                            parents: Optional[list] = None) -> dict:
    """构造"派生配方"来源信息。

    parents[0] 一般是母图；compose 这类多父图配方的 parents 按 spec 里的顺序排列。
    """
    return {
        'kind': 'recipe',
        'builder': str(builder),
        'spec': dict(spec or {}),
        'parents': [dict(p) for p in (parents or []) if p],
    }


def _register_recipe(out, builder: str, spec: dict, parents: list) -> 'pygame.Surface':
    """内部：把派生结果登记成配方（父图来源未知时不登记，让序列化器走像素兜底）"""
    infos = [get_surface_source_info(p) for p in parents]
    if any(i is None for i in infos):
        return out
    _register_surface_source(out, make_recipe_source_info(builder, spec, infos))
    return out


# ------------------------------------------------------------
# 地图瓦片来源（pytmx）
# ------------------------------------------------------------
# 地形是"地图文件 + gid"生成的：把这条来源登记上，存档里每块地块就只占一行
# {"resource_format": "map_tile", "map": "terrain/map.tmx", "gid": 12}，
# 而不是 ~1KB 的压缩像素。实测 test 地图 11400 块地块：
#   像素写法 12.7MB / 序列化 6.7s  ->  map_tile 写法约 1MB / <1s。
_MAP_TILE_LOADER = None


def register_map_tile_loader(loader) -> None:
    """登记"按 (地图路径, gid) 取瓦片贴图"的加载器（由 setup.load_tmx_data 注入）"""
    global _MAP_TILE_LOADER
    _MAP_TILE_LOADER = loader


def make_map_tile_source_info(map_path: str, gid: int) -> dict:
    """构造"地图瓦片"来源信息"""
    return {'kind': 'map_tile', 'map': _normalize_path(map_path), 'gid': int(gid)}


def register_map_tile_source(surf: 'pygame.Surface', map_path: str, gid) -> bool:
    """登记一块地块贴图的来源（幂等；已有更精确来源时不覆盖）"""
    if surf is None or not isinstance(surf, pygame.Surface):
        return False
    if not map_path or gid is None:
        return False
    try:
        entry = _surf_to_source.get(id(surf))
        if entry is not None and entry[0] is surf:
            return True
        _register_surface_source(surf, make_map_tile_source_info(map_path, gid))
        return True
    except Exception:
        return False


def normalize_path(path: str) -> str:
    """公开的路径规范化（assets 相对、POSIX 风格）—— 供 setup 等模块对齐缓存键"""
    return _normalize_path(path)


def flip_surface(surf: 'pygame.Surface',
                 flip_x: bool = True, flip_y: bool = False) -> Optional['pygame.Surface']:
    """翻转 Surface 并登记配方（替代裸用 pygame.transform.flip）"""
    if surf is None:
        return None
    out = pygame.transform.flip(surf, bool(flip_x), bool(flip_y))
    return _register_recipe(out, 'flip', {'x': bool(flip_x), 'y': bool(flip_y)}, [surf])


def scale_surface(surf: 'pygame.Surface',
                  size: Tuple[int, int]) -> Optional['pygame.Surface']:
    """缩放 Surface 并登记配方"""
    if surf is None:
        return None
    out = pygame.transform.scale(surf, (int(size[0]), int(size[1])))
    return _register_recipe(out, 'scale', {'size': [int(size[0]), int(size[1])]}, [surf])


def compose_surfaces(base: 'pygame.Surface', layers: list) -> Optional['pygame.Surface']:
    """在 base 的副本上按层叠加并登记配方（替代裸用 copy()+blit）。

    layers: [{'src': Surface, 'crop': rect|None, 'scale': (w,h)|None, 'pos': (x,y)}, ...]
    crop 为相对 src 左上角的裁剪矩形；scale 在 crop 之后应用；pos 是 blit 目标位置。
    """
    if base is None:
        return None
    out = base.copy()
    specs = []
    parents = [base]
    for layer in layers:
        src = layer.get('src')
        if src is None:
            continue
        crop = layer.get('crop')
        scale = layer.get('scale')
        pos = layer.get('pos') or (0, 0)
        piece = src
        try:
            if crop is not None:
                piece = src.subsurface(pygame.Rect(crop))
            if scale:
                piece = pygame.transform.scale(piece, (int(scale[0]), int(scale[1])))
            out.blit(piece, (int(pos[0]), int(pos[1])))
        except Exception as e:
            # 拼不上就保持"没叠这一层"，并明确告警（不产生半成品配方）
            print(f"[ResourceManager] compose_surfaces 叠加失败，已跳过该层: {e}")
            continue
        parents.append(src)
        specs.append({
            'crop': list(crop) if crop is not None else None,
            'scale': [int(scale[0]), int(scale[1])] if scale else None,
            'pos': [int(pos[0]), int(pos[1])],
        })
    return _register_recipe(out, 'compose', {'layers': specs}, parents)


def _build_flip(spec: dict, parents: list):
    return pygame.transform.flip(parents[0], bool(spec.get('x')), bool(spec.get('y')))


def _build_scale(spec: dict, parents: list):
    size = spec.get('size') or (0, 0)
    return pygame.transform.scale(parents[0], (int(size[0]), int(size[1])))


def _build_rotate(spec: dict, parents: list):
    return pygame.transform.rotate(parents[0], float(spec.get('angle') or 0.0))


def _build_compose(spec: dict, parents: list):
    out = parents[0].copy()
    for i, layer in enumerate(spec.get('layers') or []):
        src_index = i + 1
        if src_index >= len(parents):
            return None
        piece = parents[src_index]
        crop = layer.get('crop')
        if crop is not None:
            rect = pygame.Rect(crop).clip(piece.get_rect())
            if rect.width <= 0 or rect.height <= 0:
                return None
            piece = piece.subsurface(rect)
        scale = layer.get('scale')
        if scale:
            piece = pygame.transform.scale(piece, (int(scale[0]), int(scale[1])))
        pos = layer.get('pos') or (0, 0)
        out.blit(piece, (int(pos[0]), int(pos[1])))
    return out


register_surface_builder('flip', _build_flip)
register_surface_builder('scale', _build_scale)
register_surface_builder('rotate', _build_rotate)
register_surface_builder('compose', _build_compose)


def get_surface_source_info(surf: 'pygame.Surface') -> Optional[dict]:
    """查询 Surface 的来源信息（序列化器的核心入口）。

    返回 None 表示"来源未知"，序列化器只能退回保存像素。
    返回的 dict 一定带有 'kind' 字段，取值 'load' / 'procedural' / 'crop'。
    """
    if not isinstance(surf, pygame.Surface):
        return None
    entry = _surf_to_source.get(id(surf))
    if entry is not None and entry[0] is surf:
        return dict(entry[1])
    # 直接查不到，尝试把它当成 subsurface 处理
    return _resolve_derived_source(surf)


def can_replay_source(info: Optional[dict]) -> bool:
    """该来源信息是否能够仅凭描述重建出等价 Surface"""
    if not isinstance(info, dict):
        return False
    kind = info.get('kind')
    if kind == 'load':
        return bool(info.get('path')) and make_splitter_from_spec(info.get('split')) is not None
    if kind == 'procedural':
        return info.get('resource_type') is not None
    if kind == 'crop':
        return can_replay_source(info.get('parent')) and bool(info.get('rect'))
    if kind == 'map_tile':
        return (bool(info.get('map')) and info.get('gid') is not None
                and _MAP_TILE_LOADER is not None)
    if kind == 'recipe':
        name = info.get('builder')
        parents = info.get('parents') or []
        if not name or name not in _SOURCE_BUILDERS or not parents:
            return False
        return all(can_replay_source(p) for p in parents)
    return False


def reload_surface_from_source(info: Optional[dict]) -> Optional['pygame.Surface']:
    """按来源信息重建一张 Surface；无法重建时返回 None（不抛异常）"""
    if not can_replay_source(info):
        return None
    kind = info['kind']

    if kind == 'load':
        splitter = make_splitter_from_spec(info.get('split'))
        scale = info.get('scale')
        colorkey = info.get('colorkey')
        frames = _load_and_register(
            path=info['path'],
            splitter=splitter,
            scale=tuple(scale) if scale else None,
            flip_x=bool(info.get('flip_x')),
            flip_y=bool(info.get('flip_y')),
            colorkey=tuple(colorkey) if colorkey else None,
        )
        idx = int(info.get('index') or 0)
        if not frames:
            return None
        if 0 <= idx < len(frames):
            return frames[idx]
        return frames[0]

    if kind == 'procedural':
        params = info.get('params')
        params = tuple(params) if isinstance(params, list) else params
        try:
            return load_procedural_surface(info['resource_type'], params)
        except (ValueError, TypeError):
            return None

    if kind == 'crop':
        parent = reload_surface_from_source(info.get('parent'))
        if parent is None:
            return None
        rect = pygame.Rect(info['rect'])
        rect = rect.clip(parent.get_rect())
        if rect.width <= 0 or rect.height <= 0:
            return None
        return parent.subsurface(rect).copy()

    if kind == 'map_tile':
        if _MAP_TILE_LOADER is None:
            return None
        try:
            return _MAP_TILE_LOADER(info.get('map'), int(info.get('gid')))
        except Exception as e:
            print(f"[ResourceManager] 重建地图瓦片失败 map={info.get('map')} "
                  f"gid={info.get('gid')}: {e}")
            return None

    if kind == 'recipe':
        builder = _SOURCE_BUILDERS.get(str(info.get('builder')))
        if builder is None:
            return None
        parents = []
        for parent_info in info.get('parents') or []:
            parent = reload_surface_from_source(parent_info)
            if parent is None:
                return None
            parents.append(parent)
        if not parents:
            return None
        try:
            return builder(dict(info.get('spec') or {}), parents)
        except Exception as e:
            print(f"[ResourceManager] 按配方 '{info.get('builder')}' 重建失败: {e}")
            return None

    return None


def register_surface_source(surf: 'pygame.Surface', info: dict) -> bool:
    """手动登记一张 Surface 的来源（供编辑器等明确知道路径的场景调用）。

    用法：
        register_surface_source(surf, {'kind': 'load', 'path': 'terrain/x.png',
                                       'split': {'mode': 'horizontal', 'count': 1},
                                       'index': 0})
    登记后该 Surface 在存档里就只占一行路径，而不是整块像素。
    """
    if not isinstance(surf, pygame.Surface) or not isinstance(info, dict):
        return False
    _register_surface_source(surf, info)
    return True


def iter_registered_sources():
    """遍历所有已登记的 (surface, 来源信息) 对（序列化器用它做像素哈希回退匹配）"""
    for _, (surf, info) in list(_surf_to_source.items()):
        yield surf, dict(info)


def get_surface_source(surf: pygame.Surface):
    """兼容旧接口：返回 ('load', (path, transform)) / ('procedural', (type, params)) / None

    新代码请优先使用 get_surface_source_info()。
    """
    info = get_surface_source_info(surf)
    if info is None:
        return None
    kind = info.get('kind')
    if kind == 'load':
        spec = info.get('split') or {}
        frame_count = int(spec.get('count', 1)) if spec.get('mode') in ('horizontal', 'vertical') else 1
        transform = {
            'scale': info.get('scale'),
            'flip_x': bool(info.get('flip_x')),
            'flip_y': bool(info.get('flip_y')),
            'frame_count': frame_count,
            'colorkey': info.get('colorkey'),
        }
        return ('load', (info.get('path'), transform))
    if kind == 'procedural':
        return ('procedural', (info.get('resource_type'), info.get('params')))
    return None


def get_metadata(resource):
    """兼容旧接口，返回 None"""
    return None


def rotate_surface(surf: pygame.Surface, angle: float) -> pygame.Surface:
    """旋转 Surface（并登记配方，使它在存档里也只占一条配方）"""
    out = pygame.transform.rotate(surf, angle)
    return _register_recipe(out, 'rotate', {'angle': float(angle)}, [surf])


def get_surf_index_from_frames(surf: pygame.Surface) -> int:
    """尽量兼容，通过逻辑ID提取索引"""
    lid = _surf_to_logical_id.get(id(surf))
    if lid and '_' in lid:
        try:
            return int(lid.rsplit('_', 1)[1])
        except ValueError:
            pass
    info = get_surface_source_info(surf)
    if info is not None and info.get('kind') == 'load':
        return int(info.get('index') or 0)
    return 0


def get_cache_key_from_surface(surf: pygame.Surface) -> Optional[tuple]:
    """返回该 Surface 的物理缓存键（来源未知时返回 None）"""
    info = get_surface_source_info(surf)
    if info is not None and isinstance(info.get('_key'), tuple):
        return info['_key']
    return None


def get_surface_key(surf: pygame.Surface) -> Optional[tuple]:
    """兼容旧接口：等价于 get_cache_key_from_surface"""
    return get_cache_key_from_surface(surf)


# ============================================================
# 8. 暴力释放（地图切换时调用）
# ============================================================

def unload_all():
    """
    卸载所有已加载的资源（物理缓存 + 逻辑映射 + 程序化缓存 + 来源登记）。
    地图切换时调用即可，简单粗暴有效。
    """
    global _source_registry_version
    _phys_cache.clear()
    _logical_map.clear()
    _surf_to_logical_id.clear()
    _procedural_phys_cache.clear()
    _surf_to_source.clear()
    _source_registry_version += 1
    # 注意：分割器注册表不清除，因为分割器对象可能被外部引用


# ============================================================
# 9. 保留的原内部绘图函数（未改动）
# ============================================================

def _create_circle_surface(center, radius):
    surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    pygame.draw.circle(surf, (255, 0, 0, 100), (radius, radius), radius)
    pygame.draw.circle(surf, (255, 0, 0, 255), (radius, radius), radius, 2)
    return surf


def _create_rect_surface(pos, size):
    if hasattr(size, 'width') and hasattr(size, 'height'):
        size = (int(size.width), int(size.height))
    elif isinstance(size, (tuple, list)) and len(size) == 2:
        size = (int(size[0]), int(size[1]))
    else:
        raise ValueError(f"size must be (width, height) or have width/height, got {type(size)}")
    if hasattr(pos, 'x') and hasattr(pos, 'y'):
        pos = (int(pos.x), int(pos.y))
    elif isinstance(pos, (tuple, list)) and len(pos) == 2:
        pos = (int(pos[0]), int(pos[1]))
    else:
        raise ValueError(f"pos must be (x, y) or have x/y, got {type(pos)}")
    surf = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(surf, (255, 0, 0, 100), (0, 0, size[0], size[1]))
    pygame.draw.rect(surf, (255, 0, 0, 255), (0, 0, size[0], size[1]), 2)
    return surf


def _create_polygon_surface(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    height = max_y - min_y
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    local_points = [(p[0] - min_x, p[1] - min_y) for p in points]
    pygame.draw.polygon(surf, (255, 0, 0, 100), local_points)
    pygame.draw.polygon(surf, (255, 0, 0, 255), local_points, 2)
    return surf


def _create_portal_surface():
    surf = pygame.Surface((50, 50), pygame.SRCALPHA)
    points = [(25, 0), (50, 25), (25, 50), (0, 25)]
    pygame.draw.polygon(surf, (128, 0, 255), points)
    pygame.draw.polygon(surf, (255, 255, 255), points, 2)
    return surf


def _create_placeholder_surface():
    surf = pygame.Surface((32, 32), pygame.SRCALPHA)
    pygame.draw.rect(surf, (255, 0, 0, 128), (0, 0, 32, 32))
    return surf

# ============================================================
# 10. 清理旧的 Command 注册（已无这些变量）
# ============================================================
# 注意：原代码中的 Command.register(...) 引用了已删除的变量，
# 直接移除即可，因为这些内部数据不再需要序列化到命令系统。
# 如果你确实需要注册某些变量，请按需添加。