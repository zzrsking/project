import json
import os
import hashlib
import logging
import sys
import traceback
from datetime import datetime
from src.core.resource_manager import (load_image,
                                       load_procedural_surface)

# ---------------------------------------------------------------------------
# 来源重建 API（resource_manager 新增）：让图片只保存"创建路径"，而不是像素。
# 若运行在旧版 resource_manager 上（没有这些函数），自动退回"保存像素"的老行为。
# ---------------------------------------------------------------------------
try:
    from src.core.resource_manager import (get_surface_source_info,
                                           can_replay_source,
                                           reload_surface_from_source,
                                           iter_registered_sources,
                                           get_source_registry_version)
    _SOURCE_API = True
except ImportError:  # pragma: no cover - 兼容旧版资源管理器
    _SOURCE_API = False

# ==================== 日志配置 ====================
_PROGRESS_ENABLED = True   # 控制台是否输出详细 DEBUG 信息（False 则只显示 INFO 及以上）

logger = logging.getLogger('Serializer')
logger.setLevel(logging.WARNING)

if not logger.handlers:
    console = logging.StreamHandler(sys.stdout)
    console_level = logging.DEBUG if _PROGRESS_ENABLED else logging.INFO
    console.setLevel(console_level)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    console.setFormatter(console_formatter)
    logger.addHandler(console)

    log_filename = "serializer.log"
    file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    logger.info(f"日志文件已创建: {log_filename}")

# ==================== 注册表 ====================
_class_registry = {}
_name_to_class = {}

def register_class(cls):
    full_name = f"{cls.__module__}.{cls.__name__}"
    if cls not in _class_registry:
        _class_registry[cls] = full_name
        _name_to_class[full_name] = cls
        logger.debug(f"[Registry] 注册类 {cls.__name__} -> '{full_name}'")
    return _class_registry[cls]

def get_class_by_name(name):
    return _name_to_class.get(name)

def get_class_name(cls):
    return _class_registry.get(cls)

def get_class_by_id(cid):
    if isinstance(cid, int):
        raise ValueError(f"旧版本整数ID {cid} 不再支持，请重新序列化数据。")
    return _name_to_class.get(cid)

def get_class_id(cls):
    return _class_registry.get(cls)

def register(cls):
    register_class(cls)
    return cls

# ==================== 黑名单 ====================
_BLACKLIST = set()

def add_blacklist(cls):
    _BLACKLIST.add(cls)
    logger.debug(f"[Blacklist] 添加黑名单: {cls.__name__}")

def remove_blacklist(cls):
    _BLACKLIST.discard(cls)

def clear_blacklist():
    _BLACKLIST.clear()

def is_blacklisted(obj):
    return type(obj) in _BLACKLIST

# ==================== 特殊类型处理器 ====================
_special_handlers = {}
_special_handler_by_name = {}

# 继承式处理器：按 MRO 匹配，供"一族类"(如 Structure 全族)共用一套处理器。
# 精确类型处理器优先；继承式仅在没有精确匹配时生效，故不改变既存行为。
_special_inherited_handlers = {}   # base_cls -> (serializer, deserializer, name)
_special_inherited_by_name = {}    # name -> (serializer, deserializer)

def register_special(cls, serializer, deserializer):
    _special_handlers[cls] = (serializer, deserializer)

def get_special_serializer(cls):
    resolved = _resolve_special(cls)
    return resolved[0] if resolved else None

def register_special_named(name, serializer, deserializer):
    _special_handler_by_name[name] = (serializer, deserializer)

def register_special_type(cls, name, serializer, deserializer):
    register_special(cls, serializer, deserializer)
    register_special_named(name, serializer, deserializer)
    register_class(cls)
    logger.info(f"[Special] 注册特殊处理器: {name} -> {cls.__name__}")

def register_special_inherited(cls, name, serializer, deserializer):
    """为一族子类注册共用处理器（按 MRO 生效）。

    与 register_special_type 的区别：它不要求逐个子类注册 —— 保存时记录的是
    这一个族名(name)，族内具体类由处理器自己在数据里写清楚（例如结构配方里的 cls）。
    """
    _special_inherited_handlers[cls] = (serializer, deserializer, name)
    _special_inherited_by_name[name] = (serializer, deserializer)
    register_class(cls)
    logger.info(f"[Special] 注册继承式处理器: {name} -> {cls.__name__} 及其子类")

def get_special_deserializer(name):
    hit = _special_handler_by_name.get(name)
    if hit is not None:
        return hit[1]
    hit = _special_inherited_by_name.get(name)
    return hit[1] if hit is not None else None

def _resolve_special(cls):
    """-> (serializer, deserializer, 存档里用的类型名)；无处理器时 None。

    精确类型优先（类型名 = 类名，与旧存档一致），其次沿 MRO 找继承式处理器。
    """
    exact = _special_handlers.get(cls)
    if exact is not None:
        return (exact[0], exact[1], cls.__name__)
    for base in cls.__mro__[1:]:
        inherited = _special_inherited_handlers.get(base)
        if inherited is not None:
            return (inherited[0], inherited[1], inherited[2])
    return None

# ==================== 序列化核心 ====================
def _serialize(obj, memo, path='root', depth=0):
    obj_id = id(obj)
    obj_type = type(obj).__name__
    logger.debug(f"{'  ' * depth}[ENTER] {path} (id={obj_id}, type={obj_type})")

    if obj_id in memo:
        ref_id = memo[obj_id]
        logger.debug(f"{'  ' * depth}[REF] {path} -> 引用 memo[{ref_id}] (对象 id={obj_id})")
        return {'__ref__': ref_id}

    if isinstance(obj, (int, float, str, bool, type(None))):
        logger.debug(f"{'  ' * depth}[BASIC] {path} = {repr(obj)}")
        return obj

    if isinstance(obj, list):
        memo[obj_id] = len(memo) + 1
        logger.debug(f"{'  ' * depth}[LIST] {path} 长度={len(obj)}, 分配 memo id={memo[obj_id]}")
        result = []
        for i, item in enumerate(obj):
            result.append(_serialize(item, memo, f"{path}[{i}]", depth+1))
        return {'__list__': result}

    if isinstance(obj, tuple):
        memo[obj_id] = len(memo) + 1
        logger.debug(f"{'  ' * depth}[TUPLE] {path} 长度={len(obj)}, 分配 memo id={memo[obj_id]}")
        result = []
        for i, item in enumerate(obj):
            result.append(_serialize(item, memo, f"{path}[{i}]", depth+1))
        return {'__tuple__': tuple(result)}

    if isinstance(obj, dict):
        memo[obj_id] = len(memo) + 1
        logger.debug(f"{'  ' * depth}[DICT] {path} 键数={len(obj)}, 分配 memo id={memo[obj_id]}")
        result = {}
        for k, v in obj.items():
            k_ser = _serialize(k, memo, f"{path}key{repr(k)}", depth+1)
            v_ser = _serialize(v, memo, f"{path}[{repr(k)}]", depth+1)
            result[k_ser] = v_ser
        return {'__dict__': result}

    if isinstance(obj, set):
        memo[obj_id] = len(memo) + 1
        logger.debug(f"{'  ' * depth}[SET] {path} 元素数={len(obj)}, 分配 memo id={memo[obj_id]}")
        result = []
        for i, item in enumerate(obj):
            result.append(_serialize(item, memo, f"{path}->{i}", depth+1))
        return {'__set__': result}

    if isinstance(obj, type):
        cls_name = get_class_name(obj)
        if cls_name is not None:
            logger.debug(f"{'  ' * depth}[CLASS_REF] {path} -> 已注册类 {obj.__name__} (Name={cls_name})")
            return {'__class_ref__': cls_name}
        else:
            logger.debug(f"{'  ' * depth}[CLASS_REF] {path} -> 未注册类 {obj.__name__}，保存名称")
            return {'__class_ref_name__': obj.__name__}

    if is_blacklisted(obj):
        logger.debug(f"{'  ' * depth}[BLACKLIST] {path} -> 跳过黑名单类 {type(obj).__name__}")
        return None

    handler = _resolve_special(type(obj))
    if handler is not None:
        ser_fn, _, special_name = handler
        logger.debug(f"{'  ' * depth}[SPECIAL] {path} -> {type(obj).__name__} (处理器 {ser_fn.__name__})")
        result = ser_fn(obj, memo)
        if isinstance(result, dict) and '__special_type__' not in result:
            # 精确处理器沿用"类名"作为类型标签（与旧存档完全一致）；
            # 继承式处理器用自己的族名（具体类写在处理器自己的数据里）。
            result['__special_type__'] = special_name
        return result

    cls = obj.__class__
    cls_name = get_class_name(cls)
    if cls_name is None:
        error_msg = f"Class {cls.__name__} is not registered and no special handler found. Path: {path}"
        logger.error(f"{'  ' * depth}[ERROR] {error_msg}")
        logger.error(f"  对象 id={obj_id}, 类型={obj_type}, memo={memo}")
        raise TypeError(error_msg)

    memo[obj_id] = len(memo) + 1
    logger.debug(f"{'  ' * depth}[INSTANCE] {path} -> {cls.__name__} (Name={cls_name}), memo id={memo[obj_id]}")
    if hasattr(obj, '__dict__'):
        attrs_summary = {k: type(v).__name__ for k, v in obj.__dict__.items() if not k.startswith('_')}
        logger.debug(f"{'  ' * depth}  属性概要: {attrs_summary}")

    attrs = {}
    if hasattr(obj, '__dict__'):
        for k, v in obj.__dict__.items():
            if k == '_Sprite__g' or k == '_Sprite__groups':
                continue
            attrs[k] = _serialize(v, memo, f"{path}.{k}", depth+1)
    else:
        for slot in getattr(obj, '__slots__', ()):
            if hasattr(obj, slot):
                attrs[slot] = _serialize(getattr(obj, slot), memo, f"{path}.{slot}", depth+1)

    return {
        '__class_id__': cls_name,
        '__attrs__': attrs
    }

def __deserialize(data, memo):
    if not isinstance(data, dict):
        logger.debug(f"[DESERIALIZE] 基本数据: {repr(data)}")
        return data

    logger.debug(f"[DESERIALIZE] 处理数据 keys: {list(data.keys())}")

    if '__ref__' in data:
        ref_id = data['__ref__']
        obj = memo[ref_id - 1] if ref_id <= len(memo) and memo[ref_id - 1] is not None else None
        logger.debug(f"[REF] 引用 id={ref_id} -> {obj} (id={id(obj) if obj else None})")
        return obj

    if '__special_type__' in data:
        special_name = data['__special_type__']
        deserializer = get_special_deserializer(special_name)
        if deserializer is None:
            err = f"No special deserializer found for name: {special_name}"
            logger.error(err)
            raise ValueError(err)
        logger.debug(f"[SPECIAL] 反序列化特殊类型: {special_name}")
        obj = deserializer(data, memo)
        return obj

    if '__class_ref__' in data:
        cls_name = data['__class_ref__']
        if isinstance(cls_name, int):
            raise ValueError(f"旧版本整数ID {cls_name} 不再支持，请重新序列化数据。")
        cls = get_class_by_name(cls_name)
        if cls is None:
            err = f"Unknown class name: {cls_name}"
            logger.error(err)
            raise ValueError(err)
        logger.debug(f"[CLASS_REF] 返回类对象: {cls.__name__}")
        return cls

    if '__class_ref_name__' in data:
        cls_name = data['__class_ref_name__']
        try:
            cls = globals()[cls_name]
        except KeyError:
            try:
                import pygame
                if hasattr(pygame.sprite, cls_name):
                    cls = getattr(pygame.sprite, cls_name)
                else:
                    raise ValueError(f"Class '{cls_name}' not found")
            except ImportError:
                raise ValueError(f"Class '{cls_name}' not found in global namespace")
        logger.debug(f"[CLASS_REF_NAME] 返回未注册类: {cls.__name__}")
        return cls

    if '__list__' in data:
        lst = []
        memo.append(lst)
        logger.debug(f"[LIST] 重建列表，memo 索引 {len(memo)-1}")
        for item in data['__list__']:
            lst.append(__deserialize(item, memo))
        return lst

    if '__tuple__' in data:
        tup = tuple(__deserialize(item, memo) for item in data['__tuple__'])
        memo.append(tup)
        logger.debug(f"[TUPLE] 重建元组，memo 索引 {len(memo)-1}")
        return tup

    if '__dict__' in data:
        d = {}
        memo.append(d)
        logger.debug(f"[DICT] 重建字典，memo 索引 {len(memo)-1}")
        for k, v in data['__dict__'].items():
            d[__deserialize(k, memo)] = __deserialize(v, memo)
        return d

    if '__set__' in data:
        s = set()
        memo.append(s)
        logger.debug(f"[SET] 重建集合，memo 索引 {len(memo)-1}")
        for item in data['__set__']:
            s.add(__deserialize(item, memo))
        return s

    if '__class_id__' in data:
        cls_name = data['__class_id__']
        if isinstance(cls_name, int):
            raise ValueError(f"旧版本整数ID {cls_name} 不再支持，请重新序列化数据。")
        cls = get_class_by_name(cls_name)
        if cls is None:
            err = f"Unknown class name: {cls_name}"
            logger.error(err)
            raise ValueError(err)

        logger.debug(f"[INSTANCE] 重建实例: {cls.__name__} (来自 {cls_name})")
        try:
            obj = object.__new__(cls)
        except TypeError:
            obj = cls.__new__(cls)

        import pygame
        if issubclass(cls, pygame.sprite.Sprite):
            logger.debug(f"  初始化 Sprite 基类")
            pygame.sprite.Sprite.__init__(obj)

        memo.append(obj)
        logger.debug(f"  新建对象 id={id(obj)}, memo 索引={len(memo) - 1}")

        for k, v in data['__attrs__'].items():
            logger.debug(f"  设置属性 {k}")
            setattr(obj, k, __deserialize(v, memo))

        return obj

    raise ValueError(f"Unknown data structure: {data}")

# ==================== 公开接口 ====================
def serialize(obj):
    _ensure_structure_persist()      # 结构配方处理器按需安装（避免导入期循环依赖）
    logger.info("========== 开始序列化 ==========")
    memo_dict = {}
    try:
        data = _serialize(obj, memo_dict, path='root', depth=0)
    except Exception as e:
        logger.error(f"序列化过程中发生异常: {e}", exc_info=True)
        logger.error(f"当前 memo 表: {memo_dict}")
        raise
    logger.info("========== 序列化完成 ==========")
    if _PROGRESS_ENABLED:
        logger.debug("Memo 映射表 (对象id -> 序号):")
        for oid, idx in memo_dict.items():
            logger.debug(f"  {oid} -> {idx}")
    return data

def deserialize(serialized_data):
    _ensure_structure_persist()
    logger.info("========== 开始反序列化 ==========")
    if isinstance(serialized_data, dict) and '__data__' in serialized_data:
        data = serialized_data['__data__']
        logger.debug("检测到旧格式包装，已自动提取 '__data__' 内容（忽略旧 registry）")
    else:
        data = serialized_data
    memo = []
    try:
        result = __deserialize(data, memo)
    except Exception as e:
        logger.error(f"反序列化过程中发生异常: {e}", exc_info=True)
        logger.error(f"当前 memo 表: {memo}")
        raise
    logger.info("========== 反序列化完成 ==========")
    return result

# ==================== 读档后置钩子 ====================
# 有些对象"读完还差最后一步"才能用：典型是 Structure —— 它与其他结构之间的连接
# 只能等所有结构都建好之后再统一重连（否则就是对象图里的循环引用）。
# 钩子在公开入口 load() 里、所有条目都反序列化完成之后统一执行。
_post_load_hooks = []

def register_post_load_hook(fn):
    """注册一个读档后置钩子 fn(objects, labels)；异常只记录日志，不影响读档结果"""
    if callable(fn) and fn not in _post_load_hooks:
        _post_load_hooks.append(fn)

def run_post_load_hooks(objects, labels=None):
    for fn in list(_post_load_hooks):
        try:
            fn(objects, labels)
        except Exception as e:
            logger.error(f"读档后置钩子 {getattr(fn, '__name__', fn)} 失败: {e}", exc_info=True)

# ==================== 图片"来源"记录的公开编解码 ====================
# 供其它模块（例如结构配方里要存 side_face Surface）复用同一套紧凑写法。
def encode_surface_source(info):
    """来源信息 -> 可 JSON 化的紧凑记录；无法表达时返回 None"""
    fn = globals().get('_compact_source_info')
    if fn is None or not isinstance(info, dict):
        return None
    try:
        return fn(info)
    except Exception as e:
        logger.warning(f"来源信息编码失败: {e}")
        return None

def decode_surface_source(record):
    """紧凑记录 -> 来源信息；无法解析时返回 None"""
    fn = globals().get('_expand_source_record')
    if fn is None or not isinstance(record, dict):
        return None
    try:
        return fn(record, [])
    except Exception as e:
        logger.warning(f"来源信息解码失败: {e}")
        return None

# ==================== 文件读写 ====================
def _read_objects(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get('format') == 'dsh_scene/1':
            logger.warning(
                f"{filename} 是【场景文件】（scene_io 写的），不是对象列表；"
                f"请改用 src.core.scene_io.load_scene(world, filename) 读取。")
            return []
        if not isinstance(data, list):
            raise ValueError(f"File {filename} does not contain a JSON array.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"读取文件 {filename} 失败: {e}，返回空列表")
        return []

    new_data = []
    for item in data:
        if isinstance(item, dict):
            if '__data__' in item:
                new_data.append({"data": item, "label": None})
            elif 'data' in item and 'label' in item:
                new_data.append(item)
            else:
                new_data.append({"data": item, "label": None})
        else:
            new_data.append({"data": item, "label": None})
    return new_data

def _write_objects(filename, data_list):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data_list, f, indent=2, ensure_ascii=False)

# ==================== 缓存 API ====================
_cache = []

def save(obj, _label=None):
    ser_data = serialize(obj)
    _cache.append({"data": ser_data, "label": _label})
    logger.info(f"对象已保存到缓存，标签: {_label}")

def save_all(objects):
    for obj in objects:
        save(obj, _label=None)

def delete(obj):
    for i, item in enumerate(_cache):
        try:
            candidate = deserialize(item['data'])
            if _objects_equal(candidate, obj):
                del _cache[i]
                logger.info(f"已从缓存删除对象 (索引 {i})")
                return
        except Exception as e:
            logger.warning(f"比较对象时出错: {e}")
            continue
    logger.warning("对象未在缓存中找到，无法删除")

def label(obj, label_text):
    for item in _cache:
        try:
            candidate = deserialize(item['data'])
            if _objects_equal(candidate, obj):
                item['label'] = label_text
                logger.info(f"已为对象设置标签: {label_text}")
                return
        except Exception as e:
            logger.warning(f"比较对象时出错: {e}")
            continue
    logger.warning("对象未在缓存中找到，无法设置标签")

def clear_cache():
    _cache.clear()
    logger.info("缓存已清空")

def delete_all():
    _cache.clear()
    logger.info("缓存已清空")

def load(filename='data.json'):
    global _cache
    _ensure_structure_persist()
    ser_list = _read_objects(filename)
    _cache = ser_list
    result = []
    for item in ser_list:
        try:
            obj = deserialize(item['data'])
            result.append([obj, item['label']])
        except Exception as e:
            logger.warning(f"反序列化文件中的对象失败: {e}")
            result.append([None, item['label']])
    # 全部读完后再做"跨对象"的收尾（结构之间的连接就靠这一步）
    run_post_load_hooks([obj for obj, _ in result], [label for _, label in result])
    logger.info(f"从 {filename} 加载了 {len(result)} 个对象")
    return result

def execute(filename='data.json'):
    _write_objects(filename, _cache)
    logger.info(f"缓存已写入 {filename}")

# ==================== 辅助比较 ====================
def _objects_equal(a, b):
    if type(a) != type(b):
        return False
    if isinstance(a, (int, float, str, bool, type(None))):
        return a == b
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_objects_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_objects_equal(a[k], b[k]) for k in a)
    handler = get_special_serializer(type(a))
    if handler is not None:
        ser_a = handler(a, {})
        ser_b = handler(b, {})
        return ser_a == ser_b
    if hasattr(a, '__dict__') and hasattr(b, '__dict__'):
        return _objects_equal(a.__dict__, b.__dict__)
    return a == b

# ==================== 特殊类型处理器（pygame 等） ====================
try:
    import pygame
    from pygame import Surface, Rect, Vector2
    from src.utils.vector import Position
    from src.game.camera import Camera
    from src.ui.elements.inventory import InventoryInterface
    add_blacklist(pygame.sprite.Group)
    add_blacklist(InventoryInterface)


    # ==================== Surface 特殊处理器 ====================
    #
    # 存档里图片的写法（优先级从高到低）：
    #   1) "load"        —— 只写【资源路径 + 切分方式 + 帧号 + 变换】，最省
    #   2) "procedural"  —— 只写【程序化类型 + 参数】
    #   3) "crop"        —— 只写【父图来源 + 裁剪矩形】
    #   4) "pixels"      —— 兜底：把像素压缩后写进去（来源实在未知时才会发生）
    #
    # 第 1~3 种都依赖 resource_manager 的来源登记表；第 4 种还额外做一次
    # "内容哈希 -> 已登记资源" 的匹配，尽量把 .copy() / 重新拼装出来的图片也救回路径。

    _pixel_source_index = {}       # {廉价内容键: [(surface, 来源信息), ...]}
    _pixel_index_version = -1      # 对应 resource_manager 的登记表版本号
    _pixel_miss_cache = {}         # id(surface) -> 版本号：查过、确认没命中
    _PIXEL_INDEX_MAX_PROBE = 4096  # 廉价键最多采样多少字节

    _SPLIT_DEFAULT = {'mode': 'horizontal', 'count': 1}
    _UNSUPPORTED = object()        # 参数无法无损 JSON 化时的哨兵

    def _lookup_source_info(surf):
        """查询 Surface 的来源（无新 API 时返回 None，即退回像素保存）"""
        if not _SOURCE_API:
            return None
        try:
            info = get_surface_source_info(surf)
        except Exception as e:
            logger.warning(f"查询 Surface 来源失败: {e}")
            return None
        if info is None:
            return None
        try:
            if not can_replay_source(info):
                # 例如自定义分割器切出来的帧：描述不完整，无法按路径重建
                logger.debug(f"[Surface] 来源不可重建，退回像素: {info.get('kind')}")
                return None
        except Exception:
            return None
        return info

    def _cheap_content_key(surf):
        """廉价内容键：(宽, 高, 采样哈希)。

        只采样最多 _PIXEL_INDEX_MAX_PROBE 字节 —— 它的作用只是**排除**：
        真命中时采样必然一致（不会漏），假阳性再用整图比对复核（不会错）。
        """
        width, height = surf.get_size()
        raw = pygame.image.tostring(surf, 'RGBA')
        if len(raw) > _PIXEL_INDEX_MAX_PROBE:
            step = max(1, len(raw) // _PIXEL_INDEX_MAX_PROBE)
            raw = raw[::step]
        return (int(width), int(height), hashlib.md5(raw).hexdigest())

    def _ensure_pixel_index():
        """建立/刷新「廉价内容键 -> 已登记资源」索引（仅在退回像素时才做）"""
        global _pixel_source_index, _pixel_index_version
        if not _SOURCE_API:
            return
        try:
            version = get_source_registry_version()
        except Exception:
            return
        if version == _pixel_index_version:
            return
        index = {}
        try:
            entries = list(iter_registered_sources())
        except Exception as e:
            logger.warning(f"遍历来源登记表失败: {e}")
            entries = []
        for known, info in entries:
            if info.get('kind') == 'crop':
                continue  # 裁片的哈希等于其父图局部，没有额外价值
            try:
                if not can_replay_source(info):
                    continue
                key = _cheap_content_key(known)
            except Exception:
                continue
            index.setdefault(key, []).append((known, info))
        _pixel_source_index = index
        _pixel_miss_cache.clear()
        _pixel_index_version = version
        logger.debug(f"[Surface] 像素回退索引已刷新：{len(index)} 个已知资源")

    def _match_known_source(surf):
        """按像素内容把「没有登记来源」的 Surface 匹配回某个已知资源。

        性能上必须吝啬：一个 64×64 的地块，整图 md5 + tostring 全量比对是毫秒级，
        而地形层动辄上万个对象（实测全量存档 6.7s 全耗在这里）。所以：
          ① 先查「这个 Surface 对象查过没」的负缓存（同一张贴图被上万个精灵共用）；
          ② 再用采样键找候选，**没有候选就立刻返回**；
          ③ 只有候选存在时才做整图逐个比对。
        """
        if not _SOURCE_API:
            return None
        surf_id = id(surf)
        try:
            _ensure_pixel_index()
        except Exception:
            return None
        if _pixel_miss_cache.get(surf_id) == _pixel_index_version:
            return None
        if not _pixel_source_index:
            return None

        try:
            key = _cheap_content_key(surf)
        except Exception:
            return None
        candidates = _pixel_source_index.get(key)
        if not candidates:
            _pixel_miss_cache[surf_id] = _pixel_index_version
            return None

        try:
            digest = hashlib.md5(pygame.image.tostring(surf, 'RGBA')).hexdigest()
        except Exception:
            return None
        for known, info in candidates:
            try:
                if hashlib.md5(pygame.image.tostring(known, 'RGBA')).hexdigest() == digest:
                    logger.debug("[Surface] 像素内容命中已知资源，改存路径")
                    return info
            except Exception:
                continue
        _pixel_miss_cache[surf_id] = _pixel_index_version
        return None

    # ---------- 来源信息 <-> 存档记录 ----------

    def _compact_source_info(info):
        """把来源信息压成最精简的 JSON 记录（默认值一律不写）"""
        kind = info.get('kind')

        if kind == 'load':
            rec = {'resource_format': 'load', 'path': info.get('path')}
            split = info.get('split') or dict(_SPLIT_DEFAULT)
            if split != _SPLIT_DEFAULT:
                rec['split'] = {k: split[k] for k in ('mode', 'count', 'cols', 'rows') if k in split}
            index = int(info.get('index') or 0)
            if index:
                rec['index'] = index
            scale = info.get('scale')
            if scale:
                rec['scale'] = [int(v) for v in scale]
            if info.get('flip_x'):
                rec['flip_x'] = True
            if info.get('flip_y'):
                rec['flip_y'] = True
            colorkey = info.get('colorkey')
            if colorkey:
                rec['colorkey'] = [int(v) for v in colorkey[:3]]
            return rec

        if kind == 'procedural':
            params = _jsonable_params(info.get('params'))
            if params is _UNSUPPORTED:
                # 参数无法无损表达 -> 老老实实保存像素
                logger.debug("[Surface] 程序化参数无法序列化，退回像素保存")
                return None
            return {
                'resource_format': 'procedural',
                'resource_type': info.get('resource_type'),
                'params': params,
            }

        if kind == 'crop':
            parent = info.get('parent')
            if not parent:
                return None
            parent_rec = _compact_source_info(parent)
            if parent_rec is None:
                return None
            rect = info.get('rect') or ()
            return {
                'resource_format': 'crop',
                'parent': parent_rec,
                'rect': [int(v) for v in rect[:4]],
            }

        if kind == 'recipe':
            # 派生配方：flip / scale / rotate / compose（见 resource_manager）
            builder = info.get('builder')
            spec = _jsonable_params(info.get('spec') or {})
            if not builder or spec is _UNSUPPORTED:
                return None
            parent_recs = []
            for parent in info.get('parents') or []:
                parent_rec = _compact_source_info(parent)
                if parent_rec is None:
                    return None
                parent_recs.append(parent_rec)
            if not parent_recs:
                return None
            return {
                'resource_format': 'recipe',
                'builder': builder,
                'spec': spec,
                'parents': parent_recs,
            }

        if kind == 'map_tile':
            # 地图地块：只写"哪张地图 + 第几号"，不写像素
            map_path = info.get('map')
            gid = info.get('gid')
            if not map_path or gid is None:
                return None
            return {
                'resource_format': 'map_tile',
                'map': map_path,
                'gid': int(gid),
            }

        return None

    def _jsonable_params(params):
        """把程序化参数（可能含 tuple/对象）转换为可 JSON 化的结构。

        遇到无法无损表达的对象时返回 _UNSUPPORTED，调用方据此退回像素保存，
        而不是写出一份"看起来能用、实际重建不出来"的参数。
        """
        if isinstance(params, (int, float, str, bool)) or params is None:
            return params
        if isinstance(params, (tuple, list)):
            items = [_jsonable_params(p) for p in params]
            if any(item is _UNSUPPORTED for item in items):
                return _UNSUPPORTED
            return items
        if isinstance(params, dict):
            converted = {str(k): _jsonable_params(v) for k, v in params.items()}
            if any(v is _UNSUPPORTED for v in converted.values()):
                return _UNSUPPORTED
            return converted
        return _UNSUPPORTED

    def _expand_pixel_params(data):
        """程序化参数还原：list -> tuple（load_procedural_surface 需要 tuple 参数）"""
        if isinstance(data, list):
            return tuple(_expand_pixel_params(v) for v in data)
        if isinstance(data, dict):
            return {k: _expand_pixel_params(v) for k, v in data.items()}
        return data

    def _expand_source_record(rec, memo):
        """存档记录 -> 完整来源信息（同时兼容 1.3.7 及更早的旧记录写法）"""
        if not isinstance(rec, dict):
            return None
        fmt = rec.get('resource_format')

        if fmt == 'load':
            path = rec.get('path') or rec.get('relative_path')
            if not path:
                return None
            info = {
                'kind': 'load',
                'path': path,
                'split': dict(rec.get('split') or _SPLIT_DEFAULT),
                'index': int(rec.get('index') or 0),
                'scale': None,
                'flip_x': bool(rec.get('flip_x')),
                'flip_y': bool(rec.get('flip_y')),
                'colorkey': None,
            }
            scale = rec.get('scale')
            if scale:
                info['scale'] = tuple(int(v) for v in scale)
            colorkey = rec.get('colorkey')
            if colorkey:
                info['colorkey'] = tuple(int(v) for v in colorkey[:3])

            # ---- 旧格式兼容：relative_path + transform{scale,frame_count,flip_*} ----
            legacy = rec.get('transform')
            if legacy is not None:
                legacy = __deserialize(legacy, memo) if isinstance(legacy, dict) and '__dict__' in legacy else legacy
                if isinstance(legacy, dict):
                    if legacy.get('scale'):
                        info['scale'] = tuple(int(v) for v in legacy['scale'])
                    if legacy.get('colorkey'):
                        info['colorkey'] = tuple(int(v) for v in legacy['colorkey'][:3])
                    info['flip_x'] = bool(legacy.get('flip_x'))
                    info['flip_y'] = bool(legacy.get('flip_y'))
                    frame_count = int(legacy.get('frame_count') or 1)
                    if 'split' not in rec:
                        info['split'] = {'mode': 'horizontal', 'count': frame_count}
            return info

        if fmt == 'procedural':
            return {
                'kind': 'procedural',
                'resource_type': rec.get('resource_type'),
                'params': _expand_pixel_params(rec.get('params') or ()),
            }

        if fmt == 'crop':
            parent = _expand_source_record(rec.get('parent'), memo)
            if parent is None:
                return None
            rect = rec.get('rect') or ()
            if len(rect) < 4:
                return None
            return {'kind': 'crop', 'parent': parent,
                    'rect': tuple(int(v) for v in rect[:4])}

        if fmt == 'recipe':
            builder = rec.get('builder')
            if not builder:
                return None
            parents = []
            for parent_rec in rec.get('parents') or []:
                parent = _expand_source_record(parent_rec, memo)
                if parent is None:
                    return None
                parents.append(parent)
            if not parents:
                return None
            return {'kind': 'recipe', 'builder': builder,
                    'spec': dict(rec.get('spec') or {}), 'parents': parents}

        if fmt == 'map_tile':
            map_path = rec.get('map')
            gid = rec.get('gid')
            if not map_path or gid is None:
                return None
            return {'kind': 'map_tile', 'map': map_path, 'gid': int(gid)}

        return None

    # ---------- 序列化 / 反序列化 ----------

    def _serialize_surface(surf: pygame.Surface, memo: dict) -> dict:
        obj_id = id(surf)

        info = _lookup_source_info(surf)
        if info is None:
            info = _match_known_source(surf)

        if info is not None:
            rec = _compact_source_info(info)
            if rec is not None:
                memo[obj_id] = len(memo) + 1
                logger.debug(f"[Surface] 按来源保存: {rec.get('resource_format')} "
                             f"{rec.get('path') or rec.get('resource_type') or ''}")
                return {'__special_data__': rec}

        return _serialize_pixels_surface(surf, memo)


    def _deserialize_surface(data: dict, memo: list) -> pygame.Surface:
        raw = data['__special_data__']
        fmt = raw.get('resource_format')

        if fmt == 'pixels' or (fmt is None and 'palette' in raw):
            return _deserialize_pixels_surface(raw)

        info = _expand_source_record(raw, memo)
        surf = None
        if info is not None and _SOURCE_API:
            try:
                surf = reload_surface_from_source(info)
            except Exception as e:
                logger.warning(f"按来源重建 Surface 失败({info.get('kind')}): {e}")
                surf = None

        if surf is None and fmt == 'load':
            # 极端兜底：来源 API 不可用时，用旧逻辑直接重新加载
            surf = _legacy_reload_surface(raw, memo)
        if surf is None and fmt == 'procedural':
            surf = _legacy_reload_surface(raw, memo)

        if surf is None:
            raise ValueError(f"无法重建 Surface（resource_format={fmt}）")

        memo.append(surf)
        return surf


    def _legacy_reload_surface(raw: dict, memo: list):
        """旧版资源管理器下的兜底重建（不依赖来源 API）"""
        fmt = raw.get('resource_format')
        if fmt == 'load':
            path = raw.get('path') or raw.get('relative_path')
            transform = raw.get('transform')
            if isinstance(transform, dict) and '__dict__' in transform:
                transform = __deserialize(transform, memo)
            transform = transform if isinstance(transform, dict) else {}
            scale = raw.get('scale') or transform.get('scale')
            colorkey = raw.get('colorkey') or transform.get('colorkey')
            flip_x = raw.get('flip_x', transform.get('flip_x', False))
            flip_y = raw.get('flip_y', transform.get('flip_y', False))
            frame_count = int(transform.get('frame_count') or 1)
            split = raw.get('split') or {}
            if split.get('mode') in ('horizontal', 'vertical'):
                frame_count = int(split.get('count') or frame_count)
            frames = load_image(
                path=path,
                scale=tuple(scale) if scale else None,
                frame_count=frame_count,
                colorkey=tuple(colorkey) if colorkey else None,
                flip_x=bool(flip_x),
                flip_y=bool(flip_y),
            )
            index = int(raw.get('index') or 0)
            return frames[index] if 0 <= index < len(frames) else frames[0]
        if fmt == 'procedural':
            params = _expand_pixel_params(raw.get('params') or ())
            return load_procedural_surface(raw['resource_type'], params)
        return None


    # ---------- 像素序列化（兜底，尽量不用） ----------

    def _serialize_pixels_surface(surf: pygame.Surface, memo: dict) -> dict:
        obj_id = id(surf)
        size = surf.get_size()
        raw_bytes = pygame.image.tostring(surf, 'RGBA')
        palette, rle = compress_pixel_data(raw_bytes, pixel_size=4)
        memo[obj_id] = len(memo) + 1
        logger.debug(f"[Surface] 来源未知，退回像素保存: {size[0]}x{size[1]}, "
                     f"调色板 {len(palette)} 色, {len(rle)} 段")
        return {
            '__special_data__': {
                'resource_format': 'pixels',
                'size': [int(size[0]), int(size[1])],
                # 调色板拼成一个字符串（每 8 个十六进制字符 = 1 个 RGBA 颜色），
                # RLE 拼成 "count,idx,count,idx,..."，比嵌套数组小很多
                'palette': ''.join(p.hex() for p in palette),
                'rle': ','.join(f"{count},{idx}" for count, idx in rle),
            }
        }


    def _deserialize_pixels_surface(raw: dict) -> pygame.Surface:
        size = tuple(raw['size'])
        palette_raw = raw['palette']
        rle_raw = raw['rle']

        # 兼容旧格式：palette 是 ["rrggbbaa", ...]，rle 是 [[count, idx], ...]
        if isinstance(palette_raw, list):
            palette_hex = list(palette_raw)
        else:
            palette_hex = [palette_raw[i:i + 8] for i in range(0, len(palette_raw), 8)]

        if isinstance(rle_raw, str):
            numbers = [int(v) for v in rle_raw.split(',') if v != '']
            rle = list(zip(numbers[0::2], numbers[1::2]))
        else:
            rle = [(int(pair[0]), int(pair[1])) for pair in rle_raw]

        pixel_data = bytearray()
        for count, idx in rle:
            pixel_data.extend(bytes.fromhex(palette_hex[idx]) * count)
        return pygame.image.fromstring(bytes(pixel_data), size, 'RGBA')


    def _serialize_rect(rect, memo):
        obj_id = id(rect)
        memo[obj_id] = len(memo) + 1
        return {
            '__special_data__': {
                'x': rect.x,
                'y': rect.y,
                'w': rect.w,
                'h': rect.h
            }
        }
    def _deserialize_rect(data, memo):
        d = data['__special_data__']
        rect = Rect(d['x'], d['y'], d['w'], d['h'])
        memo.append(rect)
        return rect


    def _serialize_position(pos, memo):
        obj_id = id(pos)
        memo[obj_id] = len(memo) + 1
        return {
            '__special_data__': {
                'x': pos.x,
                'y': pos.y,
                'width': pos.width,
                'height': pos.height
            }
        }
    def _deserialize_position(data, memo):
        d = data['__special_data__']
        pos = Position(d['x'], d['y'], d['width'], d['height'])
        memo.append(pos)
        return pos


    def _serialize_vector2(vec, memo):
        obj_id = id(vec)
        memo[obj_id] = len(memo) + 1
        return {
            '__special_data__': {
                'x': vec.x,
                'y': vec.y,
            }
        }
    def _deserialize_vector2(data, memo):
        d = data['__special_data__']
        vec = Vector2(d['x'], d['y'])
        memo.append(vec)
        return vec


    def _serialize_camera(cam, memo):
        obj_id = id(cam)
        memo[obj_id] = len(memo) + 1
        return {
            '__special_data__': {
                'x': cam.x,
                'y': cam.y,
                'target_zoom': cam.target_zoom,
                'screen_size': cam.screen_size,
                'screen_center': cam.screen_center,
                'lerp_speed': cam.lerp_speed,
                'smoothness': cam.smoothness,
                'min_zoom': cam.min_zoom,
                'max_zoom': cam.max_zoom,
                'name': cam.name,
            }
        }

    def _deserialize_camera(data, memo):
        d = data['__special_data__']
        cam = Camera(d['smoothness'])
        cam.screen_size = d['screen_size']
        cam.screen_center = d['screen_center']
        cam.x = d['x']
        cam.y = d['y']
        cam._target_x = cam.x
        cam._target_y = cam.y
        cam.target_zoom = d['target_zoom']
        cam.zoom = d['target_zoom']
        cam.lerp_speed = d['lerp_speed']
        cam.min_zoom = d['min_zoom']
        cam.max_zoom = d['max_zoom']
        cam.name = d['name']
        memo.append(cam)
        return cam

    register_special_type(Surface, 'Surface', _serialize_surface, _deserialize_surface)
    register_special_type(Rect, 'Rect', _serialize_rect, _deserialize_rect)
    register_special_type(Position, 'Position', _serialize_position, _deserialize_position)
    register_special_type(Vector2, 'Vector2', _serialize_vector2, _deserialize_vector2)
    register_special_type(Camera, 'Camera', _serialize_camera, _deserialize_camera)

    register_class(pygame.sprite.Group)

except ImportError as e:
    logger.warning(f"部分特殊类型未加载（可能缺少 pygame 或项目模块）: {e}")

# ==================== 结构（Structure）配方接入 ====================
# Structure 一族不进对象图，改走"创建配方 + 连接表"（见 structure_persist 模块头注释）。
#
# 这里**延迟安装**，不在导入期拉 build_game：那条链会连带 import 对话/实体模块，
# 在"setup 先被导入"等启动顺序下会撞上循环导入，结果是结构静默地没法存档。
# 改成首次 serialize/deserialize 时安装 —— 那时游戏已经跑起来，所有模块都已就绪。
_structure_persist_ready = False
_structure_persist_warned = False

def _ensure_structure_persist():
    global _structure_persist_ready, _structure_persist_warned
    if _structure_persist_ready:
        return True
    try:
        from src.core.build_game import structure_persist
        structure_persist.install(sys.modules[__name__])
        _structure_persist_ready = True
        return True
    except Exception as e:
        if not _structure_persist_warned:
            _structure_persist_warned = True
            logger.warning(f"Structure 存档支持未加载（结构将无法被保存/读取）: {e}")
        return False

# ==================== 像素压缩工具 ====================
def compress_pixel_data(raw_data, pixel_size=4):
    """把 RGBA 原始字节压成 (调色板, RLE 段)。

    调色板查重改用 dict（原来是 list.index，大图上是 O(n²)）。
    """
    if not raw_data:
        return [], []
    palette = []
    index_of = {}
    indices = []
    for i in range(0, len(raw_data), pixel_size):
        pixel_block = raw_data[i: i + pixel_size]
        idx = index_of.get(pixel_block)
        if idx is None:
            idx = len(palette)
            index_of[pixel_block] = idx
            palette.append(pixel_block)
        indices.append(idx)
    rle_data = []
    count = 1
    for i in range(1, len(indices)):
        if indices[i] == indices[i - 1]:
            count += 1
        else:
            rle_data.append((count, indices[i - 1]))
            count = 1
    rle_data.append((count, indices[-1]))
    return palette, rle_data