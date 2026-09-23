import json
import threading
from typing import Any, List, Tuple

import pygame
import pytmx
import os
import math
import logging
from pygame import Vector2, display
from pygame import K_TAB, KEYDOWN, K_a, K_f, K_b, K_q
from pygame.constants import K_F1, K_F2, K_F3

from src.game.entity import Player, NPC, GameObject, WallObject, Portal, Particle, Tiled
from src.game.camera import Camera
from src.game.block import Block
from src.core.state import GameMode
from src.core.command import Command
from src.core import task_queue
from src.core.resource_manager import get_resource_path
from src.item.item import Item
from pytmx.util_pygame import load_pygame
from src.dialogue.manager import DialogueManager
from src.dialogue.condition_registry import ConditionRegistry
import settings
import setup
from src.utils.helpers import print_info, UniqueList, load_dialogue_as_objects
from src.utils.helpers import lerp_vector
from src.utils.layer_v2 import (LayerStack, LayerSpec, LayerKind,
                                configured_special_specs)
from src.editor import factory
from src.ui.elements.fonts import get_font


# ----------------------------------------------------------------------
# Layer v2 —— 引擎层按需建立。
# 世界初始化只建特殊层(default/test,见 settings)；characters/objects/collision/
# effects 等引擎层在首次 add_game_object 用到时才自动按需建（不预建空层）。
# 地图层(TMX)由 application 用 LayerSpec.from_tmx 叠加。
# ----------------------------------------------------------------------
def default_layer_specs() -> "list[LayerSpec]":
    """初始化层 = 特殊层(default/test) 仅此而已；其余按需建。"""
    return configured_special_specs()



class GameWorld:
    def __init__(self, screen_size):
        self.screen_size = screen_size
        self.screen_rect = pygame.Rect(0, 0, self.screen_size[0], self.screen_size[1])
        self.grid_size = settings.grid_size
        self.camera = settings.main_camera
        self.backend_camera = settings.free_camera
        self.camera.set_screen_size(*screen_size)
        self.backend_camera.set_screen_size(*screen_size)
        self.physics = settings.physics
        self.layers = LayerStack(default_layer_specs())
        tmx_data = pytmx.load_pygame(get_resource_path("terrain\\map.tmx"))
        Command.register(tmx_data)

        self.spatial_grid = {}           # (col,row) -> [obj, ...]
        self._obj_cells = {}             # obj -> 它当前挂载的区块坐标元组（反查表）
        self.block_size = (400, 400)
        self.block = Block(self.block_size)

        # 几何/对象集合版本号：对象增删(影响静态地形集合)时递增，
        # 供渲染器决定何时重建静态几何缓存（玩家每帧移动不递增）。
        self._geom_revision = 0

        # ---- 活跃集合/锁定候选缓存 ----
        # _active_objects: 上一帧活跃区块内的对象集合（用于只翻转变化的那部分标志）
        # _active_dirty  : 网格内容变了(增删/跨区块)或视口跨了区块 -> 需要重算活跃集合
        # _active_revision: 活跃集合版本号，供锁定候选缓存判断是否失效
        self._active_objects = None
        self._active_dirty = True
        self._active_revision = 0
        self._lock_candidates = None
        self._lock_cache_key = None
        self._lock_target = None         # 上一帧的锁定目标（锁定切换时做特效/连线进出）

        self.active_player = None

        # 对话相关
        self.dialogue_manager = None
        self.dialogue_start_callback = None
        self.dialogue_update_callback = None
        self.dialogue_end_callback = None
        self._event_callback = None

        self.container_inventory = None

        # ---- UI 引用（由 UIManager 注入） ----
        self.ui_ref = None   # 指向 DialogueInterface 实例，用于打开/关闭弹窗

        self._setup_default_objects()
        self.prev_rect = self.active_player.world_position.copy() if self.active_player else None
        self.pending_portal_lines = []

        # 注册默认条件（可在外部提前注册，这里仅为演示）
        self._register_default_conditions()

    @staticmethod
    def _register_default_conditions():
        """注册游戏内所有条件（可放在单独模块）"""
        if ConditionRegistry.is_registered("level_ge_5"):
            return

        @ConditionRegistry.register("level_ge_5", details={"level": 5})
        def check_level_ge_5(player, game):
            return player.level >= 5

        @ConditionRegistry.register("level_ge_3", details={"level": 3})
        def check_level_ge_3(player, game):
            return player.level >= 3

        @ConditionRegistry.register("has_torch", details={"items": {"火把": 1}})
        def check_has_torch(player, game):
            return player.inventory.get("火把", 0) >= 1

        @ConditionRegistry.register("has_health_potion", details={"items": {"生命药水": 1}})
        def check_has_health_potion(player, game):
            return player.inventory.get("生命药水", 0) >= 1

        # 可以继续添加其他条件...

    # ---------- 回调设置 ----------
    def set_dialogue_callbacks(self, start_cb, update_cb, end_cb, event_cb=None):
        self.dialogue_start_callback = start_cb
        self.dialogue_update_callback = update_cb
        self.dialogue_end_callback = end_cb
        self._event_callback = event_cb

    # ---------- 初始化 ----------
    def _setup_default_objects(self):
        player = factory.create_character_object(self.camera.screen_to_world((self.screen_size[0]//2, self.screen_size[1]//2)))
        gold = Item("gold", "金币", None, quantity=1000)
        self.add_game_object(player, "characters")
        player.inventory.add_item(gold)
        player.level = 100
        self.active_player = player
        self.camera.change_bind(player)
        self.prev_rect = player.world_position.copy()

    def add_game_object(self, obj, layer="default"):
        """统一添加对象到世界：加入图层、物理引擎、空间网格。

        引擎层按需建：目标层不存在时自动创建（kind 按层名推断，order 追加到顶部）。

        注意 is_collidable 用 getattr 取：读档时加入的对象种类很杂（例如
        TablePlantTile 这类没有该属性的装饰精灵），直接 `obj.is_collidable`
        会抛 AttributeError —— 而调用方（ui_manager.load_func）是在循环里逐个
        add_game_object，一次抛异常会**中断整次读档**，表现为"后面的东西全没重建"。
        """
        layer = layer or "default"
        self._ensure_layer(layer)
        self.layers.add(layer, obj)
        if getattr(obj, "is_collidable", False):
            self.physics.add_object(obj)
        self.register_object_to_grid(obj)
        self._geom_revision += 1

    def _ensure_layer(self, name: str) -> None:
        """层不存在则按需创建（不预建空层）。"""
        if name not in self.layers:
            self.layers.add_layer(self._spec_for_layer(name))

    def _spec_for_layer(self, name: str) -> LayerSpec:
        top = max((b.spec.order for b in self.layers), default=-1)
        low = (name or "").lower()
        kind = LayerKind.OBJECT
        if low in ("effects", "effect", "fx", "particles"):
            kind = LayerKind.EFFECT
        elif "ground" in low or low in ("terrain", "bg", "background", "floor"):
            kind = LayerKind.TERRAIN
        return LayerSpec(name=name, kind=kind, order=top + 1,
                         y_sorted=(low == "characters"))

    def remove_game_object(self, obj):
        self._geom_revision += 1
        self.physics.remove_object(obj)
        self.unregister_object_from_grid(obj)
        bucket = self.layers.find_bucket_of(obj)
        if bucket is not None:
            bucket.remove(obj)
        obj.kill()

    def clear_all(self):
        """清除当前世界所有对象（仅本世界 LayerStack，不波及全局）。"""
        for bucket in list(self.layers):
            for obj in list(bucket.sprites):
                self.remove_game_object(obj)
        self.physics.remove_all()
        self.spatial_grid.clear()
        self._obj_cells.clear()
        self._active_objects = None
        self._active_dirty = True
        self._active_revision += 1
        self._invalidate_lock_cache()

    @property
    def geom_revision(self) -> int:
        """几何/对象集合版本号（渲染器据此重建静态地形缓存）。"""
        return self._geom_revision

    # ---------- 空间网格管理 ----------
    # 旧实现每帧对所有物理对象调 unregister+register：unregister 会遍历【整张网格】
    # 再重建整个字典，复杂度 O(物理对象数 × 网格条目数)。现在每个对象记住自己挂在
    # 哪些区块（_obj_cells 反查表），重挂只碰它自己那几个桶；位置没跨区块就 O(1) 返回。
    def register_object_to_grid(self, obj, cells=None):
        """把对象挂到它覆盖的区块上。cells 可复用调用方已算好的坐标元组。"""
        if cells is None:
            cells = self.block.cells_of_object(obj)
        if self._obj_cells.get(obj) == cells:
            return False
        self.unregister_object_from_grid(obj)
        grid = self.spatial_grid
        for key in cells:
            bucket = grid.get(key)
            if bucket is None:
                grid[key] = [obj]
            else:
                bucket.append(obj)
        self._obj_cells[obj] = cells
        self._active_dirty = True
        return True

    def unregister_object_from_grid(self, obj):
        """只从该对象自己记录的区块里摘掉（不再全网格扫描 + 重建字典）。"""
        cells = self._obj_cells.pop(obj, None)
        if not cells:
            return False
        self._detach_from_cells(obj, cells)
        self._active_dirty = True
        return True

    def _detach_from_cells(self, obj, cells):
        grid = self.spatial_grid
        for key in cells:
            bucket = grid.get(key)
            if not bucket:
                continue
            try:
                bucket.remove(obj)
            except ValueError:
                continue
            if not bucket:
                del grid[key]          # 空桶即时回收，避免网格长胖
        return True

    def update_object_from_grid(self, obj):
        """对象跨区块时重挂；仍在原区块内直接返回（每帧只做一次坐标→区块计算）。"""
        cells = self.block.cells_of_object(obj)
        if self._obj_cells.get(obj) == cells:
            return False
        return self.register_object_to_grid(obj, cells)

    def sync_moved_objects_from_grid(self):
        """把移动过的物理对象重挂到网格（没动的对象 O(1) 跳过）。"""
        block = self.block
        cells_of = block.cells_of_rect
        obj_cells = self._obj_cells
        for obj in self.physics.body_map:
            wp = obj.world_position
            cells = cells_of(wp.x, wp.y, wp.x + wp.width, wp.y + wp.height)
            if obj_cells.get(obj) != cells:
                self.register_object_to_grid(obj, cells)

    def objects_at(self, world_pos):
        """点查询：返回该世界坐标点所在区块内的对象（编辑器拾取用）。

        旧实现（EditorManager._update_hover）每帧遍历所有图层的所有对象做 collidepoint，
        上万个对象/帧；这里只取点所在区块的桶，规模降到几十个。
        """
        return self.spatial_grid.get(self.block.cell_at(world_pos[0], world_pos[1]), ())

    def notify_object_moved(self, obj):
        """对象位置被外部直接改动后调用（编辑器/脚本）：重挂网格 + 让渲染几何缓存失效。

        不做的后果：不可碰撞的对象永远不会被 sync_moved_objects_from_grid 覆盖到，
        网格里会留在旧区块；静态地形(Tiled)渲染缓存也不会重建，画面与数据脱节。
        """
        changed = self.update_object_from_grid(obj)
        self._geom_revision += 1
        return changed

    # ---------- 区块更新（轻量化：只在活跃集合真的变化时才翻转标志） ----------
    def update_blocks(self, viewport_rect):
        """按视口把“活跃块”内的对象置 in_active_block=True，离开的置 False。

        轻量化三级：
          1. 视口没跨区块、网格也没变动 -> 整帧 O(1) 直接返回（静止时几乎零开销）；
          2. 只在活跃集合真的不同时才去翻转标志（差集更新，不再全量复位）；
          3. 活跃集合变化时递增版本号，锁定候选缓存据此失效。
        """
        viewport_changed = self.block.update_viewport(viewport_rect)
        if not viewport_changed and not self._active_dirty:
            return
        self._active_dirty = False

        new_active = set()
        grid = self.spatial_grid
        for coord in self.block.active_block_coords:
            bucket = grid.get(coord)
            if bucket:
                new_active.update(bucket)

        old_active = self._active_objects
        if old_active == new_active:
            return
        self._active_objects = new_active
        self._active_revision += 1
        self._invalidate_lock_cache()

        if old_active is None:
            turn_off = ()
            turn_on = new_active
        else:
            turn_off = old_active - new_active
            turn_on = new_active - old_active
        for obj in turn_off:
            obj.in_active_block = False
        for obj in turn_on:
            obj.in_active_block = True

    # ---------- 锁定候选（活跃区块 + 外扩一圈，去重后缓存） ----------
    def _invalidate_lock_cache(self):
        self._lock_candidates = None
        self._lock_cache_key = None

    def lockable_candidates(self):
        """返回当前可锁定对象列表（活跃区块 + 外扩一圈，去重、缓存）。

        旧实现每帧把所有图层的对象拼成一个大 list 传给锁定扫描；现在改成
        从空间网格按区块收集，并且只在活跃集合/对象增删变化时重算一次。
        外扩一圈保证“锁定距离(300) < 区块尺寸(400)”，屏幕内玩家的锁定额度不受影响。
        """
        key = (self._active_revision, self._geom_revision)
        cached = self._lock_candidates
        if cached is not None and self._lock_cache_key == key:
            return cached

        grid = self.spatial_grid
        seen = set()
        candidates = []
        append = candidates.append
        for coord in self.block.margin_block_coords:
            bucket = grid.get(coord)
            if not bucket:
                continue
            for obj in bucket:
                if not getattr(obj, "is_lockable", False):
                    continue
                oid = id(obj)
                if oid in seen:
                    continue          # 对象可跨多个区块，去重
                seen.add(oid)
                append(obj)

        self._lock_candidates = candidates
        self._lock_cache_key = key
        return candidates

    def draw_active_blocks(self, surface):
        font = get_font(20, None)
        camera = self.camera
        for block_rect, (col, row) in zip(self.block.active_blocks, self.block.active_block_coords):
            screen_rect = pygame.Rect(
                (block_rect.x - camera.offset[0]) * camera.zoom,
                (block_rect.y - camera.offset[1]) * camera.zoom,
                block_rect.width * camera.zoom,
                block_rect.height * camera.zoom
            )
            pygame.draw.rect(surface, (0, 255, 0), screen_rect, 1, 5)
            text = font.render(f"({col},{row})", True, (255, 255, 255))
            surface.blit(text, (screen_rect.left, screen_rect.top))

    def draw_depths(self, surface, camera, limit=300):
        """深度调试：把参与排序的 depth 画出来（洋红框=绘制范围，青线=depth 基准线，数字=depth）。

        depth 口径与渲染器一致：普通对象 `depth_y(obj)`；遮挡结构按叶子用
        `obj.leaf_depth(leaf)`（= 结构基准线 + layer_offset）。地形瓦片跳过（上万块纯噪音）。
        """
        from src.utils.layer_v2 import depth_y, leaf_sprites_of, is_occluder
        font = get_font(14, None)
        drawn = 0
        for bucket in self.layers.iter_visible():
            for obj in bucket.sprites:
                if drawn >= limit or isinstance(obj, Tiled):
                    continue
                if not getattr(obj, "in_active_block", True):
                    continue
                composite = is_occluder(obj)
                for leaf in (leaf_sprites_of(obj) if composite else (obj,)):
                    wp = getattr(leaf, "world_position", None)
                    if wp is None or wp.width <= 0 or wp.height <= 0:
                        continue
                    if composite:
                        depth = obj.leaf_depth(leaf) if hasattr(obj, "leaf_depth") else depth_y(leaf)
                    else:
                        depth = depth_y(obj)
                    x0, y0 = camera.world_to_screen((wp.left, wp.top))
                    x1, y1 = camera.world_to_screen((wp.right, wp.bottom))
                    box = pygame.Rect(int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0)))
                    pygame.draw.rect(surface, (255, 0, 255), box, 1)
                    y = int(camera.world_to_screen((wp.centerx, depth))[1])
                    pygame.draw.line(surface, (0, 255, 255), (box.left, y), (box.right, y), 1)
                    surface.blit(font.render(f"{depth:.0f}", True, (255, 0, 255)),
                                 (box.left, box.top - 13))
                    drawn += 1
                    if drawn >= limit:
                        break
        return drawn

    # ---------- 主更新 ----------
    def update(self, dt, game_mode):
        if game_mode == GameMode.PLAY:
            self._update_gameplay(dt)
        # 其他模式无需更新游戏逻辑

    # ---------- 游戏逻辑更新 ----------
    def _update_gameplay(self, dt):
        if self.active_player is None:
            return

        keys = pygame.key.get_pressed()

        # ---- 输入方向采集（由 World 负责） ----
        move_dir = Vector2(0, 0)
        if keys[pygame.K_LEFT]:   move_dir.x -= 1
        if keys[pygame.K_RIGHT]:  move_dir.x += 1
        if keys[pygame.K_UP]:     move_dir.y -= 1
        if keys[pygame.K_DOWN]:   move_dir.y += 1

        # ---- 玩家自主计算速度（内部使用自身 accel/friction/max_speed） ----
        self.active_player.apply_movement(move_dir, dt, self.physics)

        # ---- 物理引擎步进与同步 ----
        self.physics.step(dt)
        self.physics.sync_positions()

        # ---- 更新空间网格（只处理跨了区块的对象，其余 O(1) 跳过） ----
        self.sync_moved_objects_from_grid()

        # ---- 玩家视觉更新（动画、方向、屏幕矩形、锁定目标，由 Player 内部处理） ----
        # 锁定候选直接从空间网格按区块取（活跃区块 + 外扩一圈）并缓存，
        # 不再每帧把所有图层对象拼成一个大 list。
        self.active_player.update_visual(dt, self.camera.offset, self.camera.zoom,
                                         self.lockable_candidates())
        self._sync_lock_target()

        # ---- 特效层更新 ----
        # self.layers["effects"].update(dt)

    # ---------- 锁定目标切换（特效/传送门连线的进出场） ----------
    def _sync_lock_target(self):
        """玩家锁定目标变化时，只做一次进出场处理。

        旧实现每帧都重建 pending_portal_lines；现在只在锁定切换时重建。
        `start/stop_continuous_effect` 是 entity 上早就留好的钩子（此前没人调用）。
        """
        player = self.active_player
        target = player.current_lock_target if player is not None else None
        if target is self._lock_target:
            return

        previous = self._lock_target
        if previous is not None:
            previous.is_locked = False
            previous.stop_continuous_effect()
        if target is not None:
            target.is_locked = True
            target.start_continuous_effect()
        self._lock_target = target

        # ---- 传送门连线（保留在 World 中） ----
        self.pending_portal_lines.clear()
        if isinstance(target, Portal) and target.target_portal is not None:
            self.pending_portal_lines.append(
                (target.world_position.center, target.target_portal.world_position.center))

    # ---------- 事件处理 ----------
    def handle_event(self, event, dt):
        if event.type == KEYDOWN:
            target_mode = None
            handled = False
            if event.key == K_TAB:
                self.switchover_player()
                handled = True
            elif event.key == K_F1:
                self.camera, self.backend_camera = self.backend_camera, self.camera
                self.camera.offset = self.camera.target_offset
                self.backend_camera.offset = self.backend_camera.target_offset
            elif event.key == K_F2:
                Command.execute("threaded(input_cmd)")
            elif event.key == K_a:
                if self.active_player is not None and self.active_player.current_lock_target:
                    if self.active_player.current_lock_target.interact_type == "enemy":
                        self.handle_attack()
                handled = True
            elif event.key == K_f:
                self._interact_with_lock_target()
                handled = True
            elif event.key == K_b:
                if self.active_player:
                    self.active_player.inventory.toggle()
                handled = True
            elif event.key == K_q:
                if self.active_player:
                    # 手动锁定：不受朝向限制，直接锁最近的候选
                    self.active_player.manual_locking = True
                    self.active_player.force_lock_nearest(self.lockable_candidates())
                handled = True

            if hasattr(self, '_pending_mode'):
                target_mode = self._pending_mode
                self._pending_mode = None
                return (True, target_mode)

            return (handled, None)
        if event.type == pygame.MOUSEWHEEL:
            factor = 1.3 if event.y > 0 else 1 / 1.3
            new_zoom = self.camera.zoom * factor
            new_zoom = max(self.camera.min_zoom, min(self.camera.max_zoom, new_zoom))
            self.camera.set_zoom(new_zoom)
            return True, None
        return (False, None)

    def _interact_with_lock_target(self):
        """F 键：重算锁定并交互。

        候选只取一次（此前 K_f 分支里候选用了嵌套推导式，同一对象会因跨区块重复）；
        并且打开 manual_locking —— 否则玩家站着不动时 toward 为 0，锁定扫描会直接清空目标，
        F 键会“看起来没反应”。手动锁定在玩家重新移动时自动解除（见 Player.apply_movement）。
        """
        player = self.active_player
        if player is None:
            return
        player.manual_locking = True
        player.update_lock_target(self.lockable_candidates())
        target = player.current_lock_target
        if target is None:
            return
        self._sync_lock_target()
        if target.interact_type in ("npc", "item", "portal"):
            self.handle_interaction(target)

    # ---------- 玩家切换 ----------
    def switchover_player(self):
        bucket = self.layers.get("characters")
        characters = list(bucket.sprites) if bucket is not None else []
        if not characters:
            return
        previous = self.active_player
        if previous in characters:
            idx = (characters.index(previous) + 1) % len(characters)
        else:
            idx = 0
        self.active_player = characters[idx]
        # 换人时把上一个玩家的锁定状态收干净（否则它的目标会一直挂着 is_locked/特效）
        if previous is not None and previous is not self.active_player:
            previous.clear_lock_target()
            previous.manual_locking = False
        self._lock_target = None
        self.pending_portal_lines.clear()
        self.camera.change_bind(self.active_player)
        return self.active_player

    # ---------- 攻击 ----------
    def handle_attack(self):
        attacker = self.active_player
        target = attacker.current_lock_target if attacker is not None else None
        if not target:
            return
        distance = attacker.world_position.center.distance_to(target.world_position.center)
        if distance <= attacker.attack_range:
            print(f"{attacker.properties.get('name', 'Player')} 攻击了 {target.properties.get('name', 'Enemy')}!")
            target.take_damage(attacker.attack_power)
        else:
            print("目标太远了！")

    # ---------- 交互 ----------
    def handle_interaction(self, target):
        """按目标类型分派交互。

        旧实现里 `elif target.interact_type in ("item","portal")` 在前、`elif == "portal"`
        在后，传送门的传送分支永远走不到（死分支）；现在按类型显式分派。
        """
        player = self.active_player
        if player is None or target is None:
            return
        distance = player.world_position.distance_to(target.world_position)
        if distance > target.interact_range:
            print("需要靠近才能交互！")
            return

        if target.interact_type == "npc":
            self.start_dialogue(target)
        elif target.interact_type == "portal":
            target.interact(player)
            target_portal = getattr(target, "target_portal", None)
            if target_portal is not None:
                # 传送走物理引擎，避免穿墙/掉落
                self.physics.teleport(player, target_portal.world_position.center)
        elif target.interact_type == "item":
            target.interact(player)
            if target.properties.get("container"):
                self.show_transfer_ui(target.inventory)

    # ---------- 对话系统（重构后） ----------
    def start_dialogue(self, npc):
        if not self.active_player:
            return
        if not hasattr(self.active_player, 'dialogue_manager'):
            self.active_player.dialogue_manager = DialogueManager(self.active_player, game=self)
        self.dialogue_manager = self.active_player.dialogue_manager
        # 由任务系统决定入口节点，默认 "start"
        entry_node = getattr(npc, 'dialogue_entry', 'start')
        self.dialogue_manager.start_dialogue(npc.dialogue_tree, entry_node_id=entry_node)
        current_node = self.dialogue_manager.current_node
        if self.dialogue_start_callback:
            self.dialogue_start_callback(current_node, npc)

    def select_dialogue_option(self, option_index):
        if not self.dialogue_manager or not self.dialogue_manager.active:
            return

        self.dialogue_manager.select_option(option_index)

        if self.dialogue_manager._state == "PENDING":
            details = self.dialogue_manager.get_pending_requirement()
            # 只有当有实际需求时才打开弹窗
            if details:
                if self.ui_ref:
                    self.ui_ref.open_popup(option_index, details)
                else:
                    # 无UI引用则直接确认
                    self.confirm_dialogue_option(option_index)
            else:
                # 无条件或条件无需材料，直接确认
                self.confirm_dialogue_option(option_index)
        else:
            current_node = self.dialogue_manager.current_node
            if self.dialogue_update_callback:
                self.dialogue_update_callback(current_node)

    def confirm_dialogue_option(self, option_index):
        """确认提交（由UI弹窗的提交按钮调用）"""
        if not self.dialogue_manager or self.dialogue_manager._state != "PENDING":
            return

        success = self.dialogue_manager.confirm_pending()
        if success:
            if self.ui_ref:
                self.ui_ref.close_popup()
            if not self.dialogue_manager.active:
                self.end_dialogue()
                return
            current_node = self.dialogue_manager.current_node
            if self.dialogue_update_callback:
                self.dialogue_update_callback(current_node)
        else:
            # 条件不足，关闭弹窗并显示飘字（可在UI层实现）
            if self.ui_ref:
                self.ui_ref.close_popup()
                # 显示飘字由 UI 负责（例如通过事件）
            self._show_floating_text("条件不足，请检查所需材料")

    def cancel_dialogue_option(self):
        """取消提交（由UI弹窗的取消按钮调用）"""
        if self.dialogue_manager:
            self.dialogue_manager.cancel_pending()
        if self.ui_ref:
            self.ui_ref.close_popup()

    def end_dialogue(self):
        if self.dialogue_manager:
            self.dialogue_manager.end_dialogue()
            self.dialogue_manager = None
        if self.dialogue_end_callback:
            self.dialogue_end_callback()
        if self.ui_ref:
            self.ui_ref.close_popup()

    def _show_floating_text(self, text):
        """临时飘字（可扩展为UI通知）"""
        print(f"[飘字] {text}")
        # 若需要，可调用UI管理器显示飘字

    # ---------- 物品转移 ----------
    def show_transfer_ui(self, container_inventory):
        self.container_inventory = container_inventory
        self.container_inventory.visible = True
        if self.active_player:
            self.active_player.inventory.visible = True

    def draw_effects(self, display, camera):
        """世界叠加特效（目前是锁定传送门时的连线）。

        `pending_portal_lines` 由 World 在"锁定目标变化"时重建并持有；
        渲染后端每帧只读它、不要 clear（否则只在切换时重建的内容会被抹掉）。
        """
        for start, end in self.pending_portal_lines:
            start_screen = camera.world_to_screen(start)
            end_screen = camera.world_to_screen(end)
            pygame.draw.line(display, (255, 255, 255), start_screen, end_screen, 2)

    def trigger_event(self, event_id):
        if hasattr(self, '_event_callback') and self._event_callback:
            self._event_callback(event_id)
        else:
            print(f"游戏事件触发: {event_id} (未设置回调)")

    def _get_players(self):
        bucket = self.layers.get("characters")
        return list(bucket.sprites) if bucket is not None else []

    Command.register(_get_players)
    import time
    Command.register(time)


