"""structure_persist.py —— Structure 的存档 / 读档（配方式）

## 为什么不按对象图存

Structure 之间、Structure 与 Port 之间本来就是**环**：

    Structure.connections ──► Structure
    Structure._parts      ──► Structure（House / Hall 聚合子结构）
    Port.structure        ──► Structure
    Port.edges[side]      ──► Port ──► structure ──► …

再加上 `pygame.sprite.Group` 已被序列化器拉黑（读档后组是空的）、每个格子的贴图
是加载 / 翻转 / 合成出来的 Surface —— 逐属性序列化既还原不出来，也会把整张贴图
写成像素。所以结构**永远不进对象图**。

## 做法：只存"它是怎么被创建出来的"

    {'kind': 'hwall', 'cls': 'src.core.build_game.horizontalwall.HorizontalWoodWall',
     'size': [6, 3], 'at': [900.0, 1250.0], 'cell': [64.0, 64.0],
     'overlap_bottom': True, 'side_face': ..., 'occlusion': 'fade', 'layer_offset': 1,
     'id': '<instance_id>', 'part_ids': {'floor': '<instance_id>'},   # 组合结构才有
     'links': [{'part': None, 'port': 'top', 'side': 'left',
                'peer_id': '<对端 instance_id>', 'peer_port': 'top_right',
                'peer_side': 'right'}]}

读档分三步，**存档里没有任何对象引用，只有 instance_id 和端口语义名 —— 环从根上不存在**：

    1) 每条配方独立重建一个 Structure（House / Hall 内部部件的连接由各自的 build 自己完成）；
    2) 所有结构都建好之后，按 links 统一重连（重复的那条会因"边已占用"自然跳过）；
    3) 重连会让主动方吸附平移（Port._snap_self_to_peer），所以最后把位置复位到重建时的
       锚点，并让每个结构 on_topology_settled() 重算贴图。

## 位置复位

`connect_to` 会把主动方整体平移去贴对端。读档时位置已由存档决定，不该被重连改写，
所以重建后记下每个结构的实际中心（包围盒中心，对 House/Hall 这类组合结构也成立），
重连完成后按差值移回去；由于移动可能级联，最多迭代几轮直到收敛。
"""

from __future__ import annotations

import importlib

import pygame

from src.core.build_game.structure import Structure

# ------------------------------------------------------------------ #
# 注册表
# ------------------------------------------------------------------ #
# [(kind, base_cls, to_recipe, from_recipe)]；按 MRO 匹配，最具体的基类优先
_KINDS = []

_STATE = {'serializer': None}

# 待重连的链接（读档期间累积，全部读完由后置钩子统一处理）
_PENDING_LINKS = []

# 防重入：桌上物件里若出现对结构自身的引用，不至于无限递归
_BUILDING_RECIPES = set()

_RESTORE_PASSES = 4       # 位置复位最多迭代轮数
_EPS = 0.01               # 位置视为"已对齐"的阈值


def register_structure_kind(kind, base_cls, to_recipe, from_recipe):
    """登记"一类结构"的配方读写。按 MRO 匹配，子类自动继承最近的那一条。"""
    for i, (_, cls, _, _) in enumerate(_KINDS):
        if cls is base_cls:
            _KINDS[i] = (str(kind), base_cls, to_recipe, from_recipe)
            return
    _KINDS.append((str(kind), base_cls, to_recipe, from_recipe))


def structure_kind_of(obj):
    """-> (kind, to_recipe, from_recipe)；未登记时 (None, None, None)"""
    for klass in type(obj).__mro__:
        for kind, base, to_recipe, from_recipe in _KINDS:
            if klass is base:
                return kind, to_recipe, from_recipe
    return None, None, None


# ------------------------------------------------------------------ #
# 工具
# ------------------------------------------------------------------ #
def _class_path(cls) -> str:
    return f"{cls.__module__}.{cls.__name__}"


def _resolve_class(path):
    """按 "模块.类名" 取类；兼容项目里并存的 src.x / x 两种导入写法。"""
    if not path:
        return None
    mod_name, _, cls_name = str(path).rpartition('.')
    if not mod_name or not cls_name:
        return None
    for candidate in (mod_name, mod_name[4:] if mod_name.startswith('src.') else f"src.{mod_name}"):
        try:
            module = importlib.import_module(candidate)
        except Exception:
            continue
        cls = getattr(module, cls_name, None)
        if cls is not None:
            return cls
    return None


def _instance_id_of(obj) -> str:
    iid = getattr(obj, 'instance_id', None)
    return str(iid) if iid is not None else ''


def _center_of(obj):
    """结构的实际中心：优先叶子包围盒中心（对组合结构也成立），退回 shape.center"""
    try:
        wp = obj.world_position
        if wp is not None and wp.width and wp.height:
            return (float(wp.centerx), float(wp.centery))
    except Exception:
        pass
    try:
        cx, cy = obj.shape.center
        return (float(cx), float(cy))
    except Exception:
        return None


def _iter_parts(obj):
    """(部件名, 部件) 列表；非组合结构为空。"""
    parts = getattr(obj, '_parts', None)
    if isinstance(parts, dict):
        return [(name, part) for name, part in parts.items() if isinstance(part, Structure)]
    return []


def _iter_hosts(obj):
    """本结构自身 + 各部件 —— 端口可能挂在部件上（House/Hall）。"""
    yield None, obj
    for name, part in _iter_parts(obj):
        yield name, part


def _named_ports(host) -> dict:
    """{id(port): 语义名}（端口语义名是跨存档稳定的寻址方式）"""
    named = {}
    try:
        ports = host.ports
    except Exception:
        return named
    if isinstance(ports, dict):
        for name, port in ports.items():
            named.setdefault(id(port), name)
    return named


def _iter_host_ports(host):
    seen = set()
    try:
        for port in host.iter_all_ports():
            if id(port) not in seen:
                seen.add(id(port))
                yield port
    except Exception:
        pass
    # 组合结构的端面口（Hall 的 _edge_ports）不在 _cell_ports_cache 里，补进来
    try:
        for _, port in (host.ports or {}).items():
            if id(port) not in seen:
                seen.add(id(port))
                yield port
    except Exception:
        pass


def _occlusion_name(obj):
    policy = getattr(obj, 'occlusion_policy', None)
    if policy is None:
        return None
    return getattr(policy, 'value', None) or str(policy).lower()


def _apply_occlusion(obj, name):
    if not name:
        return
    try:
        from src.utils.layer_v2 import OcclusionPolicy
        policy = OcclusionPolicy(str(name))
    except Exception:
        return
    try:
        obj.occlusion_policy = policy
    except Exception:
        try:
            obj._occlusion_policy = policy
        except Exception:
            pass


def _side_face_spec(obj):
    """side_face 可能是 House 编号字符串，也可能是 Surface —— 两种都要能重建。"""
    side_face = getattr(obj, 'side_face', None)
    if side_face is None:
        return None
    serializer = _STATE['serializer']
    if isinstance(side_face, pygame.Surface):
        encoded = serializer.encode_surface_source(side_face) if serializer else None
        if encoded is None:
            _warn("side_face 是 Surface 但查不到来源，读档后将丢失侧脸")
            return None
        return {'kind': 'surface', 'source': encoded}
    return {'kind': 'id', 'value': str(side_face)}


def _decode_side_face(spec):
    if not isinstance(spec, dict):
        return None
    if spec.get('kind') == 'id':
        return str(spec.get('value'))
    serializer = _STATE['serializer']
    if spec.get('kind') == 'surface' and serializer:
        return serializer.decode_surface_source(spec.get('source'))
    return None


def _warn(message):
    print(f"[structure_persist] {message}")


# ------------------------------------------------------------------ #
# 链接：结构之间的连接关系（存 ID + 语义名，不存对象）
# ------------------------------------------------------------------ #
def _collect_links(obj) -> list:
    """收集"跨结构"的连接。

    组合结构（House/Hall）内部部件之间的连接**不收录**：它们由各自的 build 每次
    重建时自己连好（2 份记录纯属噪声，还会让"重建后为何已存在"的日志难以阅读）。
    """
    internal_ids = {_instance_id_of(obj)}
    for _, part in _iter_parts(obj):
        internal_ids.add(_instance_id_of(part))

    links = []
    for part_name, host in _iter_hosts(obj):
        named = _named_ports(host)
        for port in _iter_host_ports(host):
            peers = getattr(port, 'peers', None) or {}
            for side, peer in list(peers.items()):
                peer_host = getattr(peer, 'structure', None)
                if peer_host is None:
                    continue
                if _instance_id_of(peer_host) in internal_ids:
                    continue        # 同一条结构的内部连接 -> 重建时自动恢复
                peer_side = None
                for peer_edge_side, peer_edge in (getattr(peer, 'edges', {}) or {}).items():
                    if peer_edge is port:
                        peer_side = peer_edge_side
                        break
                if peer_side is None:
                    continue
                peer_named = _named_ports(peer_host)
                links.append({
                    'part': part_name,
                    'port': named.get(id(port)),
                    'cell': list(port.cell) if getattr(port, 'cell', None) is not None else None,
                    'side': side,
                    'peer_id': _instance_id_of(peer_host),
                    'peer_part': None,
                    'peer_port': peer_named.get(id(peer)),
                    'peer_cell': list(peer.cell) if getattr(peer, 'cell', None) is not None else None,
                    'peer_side': peer_side,
                })
    return links


def _index_structures(objects) -> dict:
    """{instance_id: 结构}，含组合结构的部件（部件 id 由配方 part_ids 还原）"""
    index = {}
    for obj in objects or []:
        if not isinstance(obj, Structure):
            continue
        iid = _instance_id_of(obj)
        if iid:
            index[iid] = obj
        for _, part in _iter_parts(obj):
            part_id = _instance_id_of(part)
            if part_id:
                index[part_id] = part
    return index


def _resolve_port(host, name, cell, side):
    """先按语义名取端口；名字缺失/失效时退回"某边的某格"。"""
    if host is None:
        return None
    if name:
        try:
            return host.port(name)
        except Exception:
            pass
    if cell is not None:
        try:
            port = host.port_at_side(side, tuple(cell))
            if port is not None:
                return port
        except Exception:
            pass
    return None


def replay_pending_connections(objects=None, labels=None) -> int:
    """把待处理的连接全部重放；返回成功连接条数。

    由 serializer 的读档后置钩子自动调用；单独 deserialize() 之后也可手动调，
    但需要把（重建出来的）结构列表传进来做 instance_id 索引。

    只有**真正涉及连接**的结构才会被要求 on_topology_settled() 重算贴图 ——
    没有任何连接的结构保持 constructor 造出来的样子（与"运行时没连过"完全一致）。
    """
    global _PENDING_LINKS
    pending, _PENDING_LINKS = list(_PENDING_LINKS), []
    if not pending:
        return 0

    index = _index_structures(objects)
    touched = {}          # id(结构) -> 结构：需要重算拓扑的那些
    connected = 0
    skipped = 0
    unresolved = 0
    for entry in pending:
        link = entry.get('link') or {}
        owner = index.get(entry.get('owner_id') or '')
        if owner is None:
            _warn(f"重连跳过：找不到宿主结构 {entry.get('owner_id')}（该结构可能没被存进同一个存档）")
            unresolved += 1
            continue
        host = owner
        if entry.get('part'):
            host = dict(_iter_parts(owner)).get(entry['part'])
            if host is None:
                _warn(f"重连跳过：{type(owner).__name__} 没有部件 {entry['part']}")
                unresolved += 1
                continue
        peer_owner = index.get(link.get('peer_id') or '')
        if peer_owner is None:
            _warn(f"重连跳过：找不到对端结构 {link.get('peer_id')}")
            unresolved += 1
            continue
        peer_host = peer_owner
        if link.get('peer_part'):
            peer_host = dict(_iter_parts(peer_owner)).get(link['peer_part'])
            if peer_host is None:
                _warn(f"重连跳过：对端 {type(peer_owner).__name__} 没有部件 {link['peer_part']}")
                unresolved += 1
                continue

        mine = _resolve_port(host, link.get('port'), link.get('cell'), link.get('side'))
        theirs = _resolve_port(peer_host, link.get('peer_port'), link.get('peer_cell'),
                               link.get('peer_side'))
        if mine is None or theirs is None:
            _warn(f"重连跳过：端口解析失败 "
                  f"({type(host).__name__}.{link.get('port')} <-> "
                  f"{type(peer_host).__name__}.{link.get('peer_port')})")
            unresolved += 1
            continue
        # 同一条连接会被两端各记一次；对端先连上时这条边的占用状态已生效，直接跳过
        already = False
        try:
            already = bool(mine.edge_connected(link.get('side')))
        except Exception:
            already = False
        if already:
            skipped += 1
            touched[id(owner)] = owner
            touched[id(peer_owner)] = peer_owner
            continue
        try:
            mine.connect_to(theirs, self_side=link.get('side'), peer_side=link.get('peer_side'))
            connected += 1
            touched[id(owner)] = owner
            touched[id(peer_owner)] = peer_owner
        except Exception as e:
            _warn(f"重连失败 {type(host).__name__}.{link.get('port')} <-> "
                  f"{type(peer_host).__name__}.{link.get('peer_port')}: {e}")

    _restore_positions(objects)
    _refresh_topology(list(touched.values()))
    _warn(f"结构重连：新建 {connected} 条，已存在 {skipped} 条，"
          f"无法解析 {unresolved} 条（共 {len(pending)} 条记录）")
    return connected


def _restore_positions(objects) -> None:
    """重连会移动主动方 —— 位置以存档为准，统一复位到重建时的锚点。"""
    targets = []
    for obj in objects or []:
        if not isinstance(obj, Structure):
            continue
        anchor = getattr(obj, '_persist_anchor', None)
        if anchor is not None:
            targets.append((obj, anchor))
    for _ in range(_RESTORE_PASSES):
        moved = False
        for obj, anchor in targets:
            current = _center_of(obj)
            if current is None:
                continue
            dx, dy = anchor[0] - current[0], anchor[1] - current[1]
            if abs(dx) < _EPS and abs(dy) < _EPS:
                continue
            mover = getattr(obj, 'move_by', None)
            if not callable(mover):
                continue
            try:
                mover(dx, dy)
                moved = True
            except Exception:
                continue
        if not moved:
            break


def _refresh_topology(objects) -> None:
    """整批连接结束后，让**参与过连接的**结构自决策重算贴图（并补回侧脸装饰）。

    没连过任何东西的结构不该被"重算"：那会把它从 constructor 造出来的样子
    改成"未连接拓扑"的样子，反而与存档前不一致。
    """
    for obj in objects or []:
        if not isinstance(obj, Structure):
            continue
        try:
            obj.on_topology_settled()
        except Exception as e:
            _warn(f"{type(obj).__name__}.on_topology_settled() 失败: {e}")
        setter = getattr(obj, 'set_side_face', None)
        spec = _decode_side_face(getattr(obj, '_persist_side_face', None))
        if callable(setter) and spec is not None:
            fill = float(getattr(obj, 'side_face_fill', None)
                         or getattr(obj, 'side_fill', None) or 1.0)
            try:
                setter(spec, fill)
            except Exception as e:
                _warn(f"{type(obj).__name__}.set_side_face() 恢复失败: {e}")


# ------------------------------------------------------------------ #
# 配方：写
# ------------------------------------------------------------------ #
def structure_recipe(obj) -> dict:
    """结构 -> 配方；未登记的类型返回 None（调用方据此报错，不静默丢对象）。"""
    kind, to_recipe, _ = structure_kind_of(obj)
    if to_recipe is None:
        return None
    if id(obj) in _BUILDING_RECIPES:
        _warn(f"检测到结构自引用，已跳过 {type(obj).__name__} 的重复配方")
        return None
    _BUILDING_RECIPES.add(id(obj))
    try:
        recipe = {
            'kind': kind,
            'cls': _class_path(type(obj)),
            'id': _instance_id_of(obj),
        }
        recipe.update(to_recipe(obj) or {})

        layer_offset = getattr(obj, 'layer_offset', None)
        if layer_offset is not None:
            recipe['layer_offset'] = int(layer_offset)
        occlusion = _occlusion_name(obj)
        if occlusion and occlusion != 'none':
            recipe['occlusion'] = occlusion
        side_face = _side_face_spec(obj)
        if side_face is not None:
            recipe['side_face'] = side_face
        parts = _iter_parts(obj)
        if parts:
            recipe['part_ids'] = {name: _instance_id_of(part) for name, part in parts}
        recipe['links'] = _collect_links(obj)
        return recipe
    finally:
        _BUILDING_RECIPES.discard(id(obj))


# ------------------------------------------------------------------ #
# 配方：读
# ------------------------------------------------------------------ #
def build_structure(recipe: dict):
    """配方 -> Structure；不支持/失败时返回 None（不抛异常，由调用方决定怎么报）。"""
    kind, _, from_recipe = structure_kind_of_recipe(recipe)
    if from_recipe is None:
        _warn(f"未知结构类型 kind={recipe.get('kind')!r}，无法重建")
        return None
    try:
        obj = from_recipe(recipe)
    except Exception as e:
        _warn(f"结构重建失败 kind={recipe.get('kind')!r}: {e}")
        return None
    if obj is None:
        return None

    instance_id = recipe.get('id')
    if instance_id:
        try:
            obj.instance_id = str(instance_id)
        except Exception:
            pass
    for name, part_id in (recipe.get('part_ids') or {}).items():
        part = dict(_iter_parts(obj)).get(name)
        if part is None or not part_id:
            continue
        try:
            part.instance_id = str(part_id)
        except Exception:
            pass

    if 'layer_offset' in recipe:
        try:
            obj.layer_offset = int(recipe['layer_offset'])
        except Exception:
            pass
    _apply_occlusion(obj, recipe.get('occlusion'))

    side_face = _decode_side_face(recipe.get('side_face'))
    obj._persist_side_face = recipe.get('side_face')
    setter = getattr(obj, 'set_side_face', None)
    if callable(setter) and side_face is not None:
        fill = float(getattr(obj, 'side_face_fill', None)
                     or getattr(obj, 'side_fill', None) or 1.0)
        try:
            setter(side_face, fill)
        except Exception as e:
            _warn(f"{type(obj).__name__}.set_side_face() 应用失败: {e}")

    # 位置锚点：重建后的实际中心 —— 重连会把主动方挪走，之后按它复位
    obj._persist_anchor = _center_of(obj)
    return obj


def structure_kind_of_recipe(recipe: dict):
    """按配方里的 cls 找最合适的 kind 读写器（子类如 Hall 自动匹配到 Hall 那条）"""
    cls = _resolve_class(recipe.get('cls'))
    if cls is not None:
        for klass in cls.__mro__:
            for kind, base, to_recipe, from_recipe in _KINDS:
                if klass is base:
                    return kind, to_recipe, from_recipe
    for kind, _, to_recipe, from_recipe in _KINDS:
        if kind == recipe.get('kind'):
            return kind, to_recipe, from_recipe
    return None, None, None


# ------------------------------------------------------------------ #
# 各类结构的配方读写
# ------------------------------------------------------------------ #
def _recipe_hwall(obj) -> dict:
    return {
        'size': [int(obj._grid_width), int(obj._grid_height)],
        'at': [float(obj.shape.centerx), float(obj.shape.centery)],
        'cell': [float(obj._cell_w), float(obj._cell_h)],
        'overlap_bottom': bool(getattr(obj, 'overlap_bottom', False)),
    }


def _build_hwall(recipe: dict):
    from src.core.build_game.horizontalwall import HorizontalWoodWall
    cls = _resolve_class(recipe.get('cls')) or HorizontalWoodWall
    size = tuple(int(v) for v in recipe.get('size') or (1, 1))
    at = tuple(float(v) for v in recipe.get('at') or (0.0, 0.0))
    overlap = bool(recipe.get('overlap_bottom'))
    try:
        return cls(size, at, overlap_bottom=overlap)
    except TypeError:
        # 无纹理壳基类需要 cell_size
        cell = tuple(float(v) for v in recipe.get('cell') or (64.0, 64.0))
        return cls(size, cell, at, overlap_bottom=overlap)


def _recipe_vwall(obj) -> dict:
    return {
        'size': [int(obj._v_length), int(obj._v_height)],
        'at': [float(obj.shape.centerx), float(obj.shape.centery)],
        'cell': [float(obj._cell_w), float(obj._cell_h)],
    }
    # 注意：orientation / _head_connected / _tail_cross 等是【连接拓扑的派生状态】，
    # 由重连后的 on_topology_settled() 自己算回来，不写进存档（写了反而可能与真实拓扑打架）。


def _build_vwall(recipe: dict):
    from src.core.build_game.verticalwall import VerticalWoodWall
    cls = _resolve_class(recipe.get('cls')) or VerticalWoodWall
    length, height = (int(v) for v in (recipe.get('size') or (2, 2)))
    at = tuple(float(v) for v in recipe.get('at') or (0.0, 0.0))
    try:
        return cls(length, height, center=at)
    except TypeError:
        cell = tuple(float(v) for v in recipe.get('cell') or (64.0, 64.0))
        return cls(length, height, cell, at)


def _recipe_floor(obj) -> dict:
    return {
        'size': [int(obj._grid_width), int(obj._grid_height)],
        'at': [float(obj.shape.centerx), float(obj.shape.centery)],
        'cell': [float(obj._cell_w), float(obj._cell_h)],
    }


def _build_floor(recipe: dict):
    from src.core.build_game.floor import BrickFloor
    cls = _resolve_class(recipe.get('cls')) or BrickFloor
    size = tuple(int(v) for v in recipe.get('size') or (1, 1))
    at = tuple(float(v) for v in recipe.get('at') or (0.0, 0.0))
    try:
        return cls(size, at)
    except TypeError:
        cell = tuple(float(v) for v in recipe.get('cell') or (64.0, 64.0))
        return cls(size, cell, at)


def _infer_use_wood(floor) -> bool:
    name = type(floor).__name__ if floor is not None else ''
    return ('Wood' in name) or name.startswith('Brick')


def _recipe_house(obj) -> dict:
    parts = dict(_iter_parts(obj))
    floor = parts.get('floor')
    length = int(getattr(floor, '_grid_width', 0) or 0)
    width = int(getattr(floor, '_grid_height', 0) or 0)
    height = 0
    for name in ('left_wall', 'right_wall'):
        part = parts.get(name)
        if part is not None:
            height = int(getattr(part, '_v_height', 0) or 0)
            if height:
                break
    if not height:
        for name in ('top_wall', 'bottom_wall'):
            part = parts.get(name)
            if part is not None:
                height = int(getattr(part, '_grid_height', 0) or 0)
                if height:
                    break
    cell = [float(getattr(floor, '_cell_w', 64) or 64), float(getattr(floor, '_cell_h', 64) or 64)]
    at = None
    if floor is not None:
        try:
            at = [float(floor.shape.centerx), float(floor.shape.centery)]
        except Exception:
            at = None
    if at is None:
        center = _center_of(obj) or (0.0, 0.0)
        at = [float(center[0]), float(center[1])]
    recipe = {
        'size': [length, width],
        'height': height,
        'cell': cell,
        'at': at,
        'use_wood': _infer_use_wood(floor),
    }
    orientation = getattr(obj, 'orientation', None)
    if orientation:
        recipe['orientation'] = str(orientation)
    return recipe


def _build_house(recipe: dict):
    from src.core.build_game.house import House
    cls = _resolve_class(recipe.get('cls')) or House
    length, width = (int(v) for v in (recipe.get('size') or (3, 3)))
    height = int(recipe.get('height') or 3)
    cell = tuple(float(v) for v in recipe.get('cell') or (64.0, 64.0))
    at = tuple(float(v) for v in recipe.get('at') or (0.0, 0.0))
    kwargs = dict(cell=cell, center=at, use_wood=bool(recipe.get('use_wood', True)))
    if recipe.get('orientation'):
        kwargs['orientation'] = str(recipe['orientation'])
    # 注意：layer_offset 不往 build() 里塞 —— House.build 收这个参数、Hall.build 不收，
    # 传进去会让 Hall 直接重建失败（TypeError: unexpected keyword argument）。
    # 它只是个普通属性，建完由 build_structure() 统一设置即可。
    try:
        return cls.build(length, width, height, **kwargs)
    except TypeError:
        kwargs.pop('orientation', None)
        return cls.build(length, width, height, **kwargs)


def _recipe_table(obj) -> dict:
    serializer = _STATE['serializer']
    objects = []
    for cell, sprite in _iter_table_objects(obj):
        if serializer is None:
            _warn("没有序列化器句柄，桌上的物件将不会被保存")
            break
        try:
            objects.append({'cell': [int(cell[0]), int(cell[1])],
                            'data': serializer.serialize(sprite)})
        except Exception as e:
            _warn(f"桌上物件序列化失败（该物件读取后会缺失）: {e}")
    return {
        'size': [int(obj._grid_width), int(obj._grid_height)],
        'at': [float(obj.shape.centerx), float(obj.shape.centery)],
        'cell': [float(obj._cell_w), float(obj._cell_h)],
        'objects': objects,
    }


def _iter_table_objects(table):
    """遍历桌面 object 层上摆着的东西：(cell, sprite)"""
    iterate = getattr(table, 'iter_layer_items', None)
    if not callable(iterate):
        return
    try:
        for _, cell, sprite in iterate(getattr(table, 'LAYER_OBJECT', 'object')):
            yield cell, sprite
    except Exception:
        return


def _build_table(recipe: dict):
    from src.core.build_game.on_the_table import WoodTable
    cls = _resolve_class(recipe.get('cls')) or WoodTable
    size = tuple(int(v) for v in recipe.get('size') or (1, 1))
    at = tuple(float(v) for v in recipe.get('at') or (0.0, 0.0))
    cell = tuple(int(v) for v in recipe.get('cell') or (64, 64))
    try:
        table = cls(size, at, cell)
    except TypeError:
        table = cls(size, at)
    serializer = _STATE['serializer']
    for entry in recipe.get('objects') or []:
        if serializer is None:
            break
        try:
            sprite = serializer.deserialize(entry.get('data'))
        except Exception as e:
            _warn(f"桌上物件反序列化失败: {e}")
            continue
        if sprite is None:
            continue
        try:
            table.place_object(tuple(entry.get('cell') or (0, 0)), sprite)
        except Exception as e:
            _warn(f"桌上物件摆放失败: {e}")
    return table


def register_default_kinds() -> None:
    """登记内置结构类型（幂等）"""
    from src.core.build_game.horizontalwall import HorizontalWall
    from src.core.build_game.verticalwall import VerticalWall
    from src.core.build_game.floor import Floor
    from src.core.build_game.house import House
    from src.core.build_game.hall import Hall
    from src.core.build_game.on_the_table import Table

    register_structure_kind('hwall', HorizontalWall, _recipe_hwall, _build_hwall)
    register_structure_kind('vwall', VerticalWall, _recipe_vwall, _build_vwall)
    register_structure_kind('floor', Floor, _recipe_floor, _build_floor)
    register_structure_kind('house', House, _recipe_house, _build_house)
    register_structure_kind('hall', Hall, _recipe_house, _build_house)
    register_structure_kind('table', Table, _recipe_table, _build_table)


# ------------------------------------------------------------------ #
# 序列化器接入
# ------------------------------------------------------------------ #
def _serialize_structure(obj, memo):
    recipe = structure_recipe(obj)
    if recipe is None:
        kind, _, _ = structure_kind_of(obj)
        raise TypeError(
            f"结构 {type(obj).__module__}.{type(obj).__name__} 没有登记存档配方"
            f"（kind={kind!r}）。请在 structure_persist.register_structure_kind() 里"
            f"补一条，否则它无法被存档。")
    memo[id(obj)] = len(memo) + 1
    return {'__special_data__': recipe}


def _deserialize_structure(data, memo):
    recipe = data.get('__special_data__') or {}
    obj = build_structure(recipe)
    if obj is None:
        raise ValueError(f"结构读档失败：{recipe.get('kind')!r} {recipe.get('cls')!r}")
    for link in recipe.get('links') or []:
        _PENDING_LINKS.append({
            'owner_id': recipe.get('id') or '',
            'part': link.get('part'),
            'link': link,
        })
    memo.append(obj)
    return obj


def install(serializer_module) -> None:
    """把结构配方接进序列化器（由 serializer 在导入尾部调用，只生效一次）。"""
    if _STATE['serializer'] is serializer_module:
        return
    _STATE['serializer'] = serializer_module
    register_default_kinds()
    serializer_module.register_special_inherited(
        Structure, 'Structure', _serialize_structure, _deserialize_structure)
    serializer_module.register_post_load_hook(replay_pending_connections)
