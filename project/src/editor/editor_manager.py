import pygame
from pygame.constants import K_F2
import math
from pygame import Vector2
from src.core.command import Command
from src.game.entity import GameObject, NPC, Player, Portal, WallObject
from src.utils.helpers import clamp, print_info
from src.item.item import Item
from src.editor.factory import create_object_from_tool
from src.editor.preview import draw_placement_preview
from src.editor.collision_editor import CollisionEditor
from src.utils.layer_v2 import is_composite, leaf_sprites_of
from src.editor.tools import is_editor_object


class EditorManager:
    def __init__(self, world, ui_manager=None):
        self.world = world
        self.ui = ui_manager
        self.selected_tool = None
        self.pre_selected_tool = None # 代表上一帧的selected_tool,而不是上一次切换的selected_tool
        self.selected_layer = None
        self.hovered_obj = None
        self.wall_edit_type = None
        self.wall_points = []
        self.temp_point = None
        self._setup_tools()
        self.catch = None
        self.preview_obj = None          # 当前预览对象
        self.collision_editor = CollisionEditor(world)
        # 临时调试工具：横墙拖拽吸附（G 键启用；F 已让给结构面板）
        from src.editor.wall_drag_tool import WallDragTool
        self.wall_drag = WallDragTool(world=world)

        # ---- 删除日志：删除的对象/结构也进统一撤销体系（Ctrl+Z 放回去）----
        # 和 structure_builder / wall_drag 走同一套"全局序号"协议（见 undo.py）
        from src.editor.delete_log import DeleteLog
        self.delete_log = DeleteLog()

        # ---- 结构编辑器（F 呼出的"结构调色盘" + 拖拽生成）----
        from src.editor.structure_palette import StructurePalette
        from src.editor.structure_builder import StructureBuilder
        self.structure_builder = StructureBuilder(world)
        self.structure_palette = StructurePalette(
            on_select=self._on_structure_chosen)

    def _layer_name_of(self, obj) -> str:
        """对象当前所在层的名字（撤销删除时要放回原层）。"""
        try:
            bucket = self.world.layers.find_bucket_of(obj)
        except Exception:
            bucket = None
        name = getattr(getattr(bucket, "spec", None), "name", None)
        return name or "default"

    def _on_structure_chosen(self, kind):
        """面板里选好结构 -> 进入拖拽放置状态。"""
        self.structure_builder.arm(kind)
        print_info(f"[structure] 待放置：{kind}（左键拖拽放置，Esc 取消）")

    def undo(self):
        """统一撤销：在【结构生成 / 横墙拖拽 / 删除】里挑最近发生的那一件撤。

        为什么要统一：这几个工具各自维护 undo_list，Ctrl+Z 绑死某一个的话，
        别的动作就永远撤不掉（删除此前正是如此：只调了 wall_drag.undo()）。
        序号由 src/editor/undo.py 统一发放，跨工具可比大小。
        """
        from src.editor.undo import newest
        seq, tool = newest(self.structure_builder, self.wall_drag, self.delete_log)
        if tool is None:
            print_info("[editor] 没有可撤销的操作")
            return False
        if seq != 0 and (tool is self.structure_builder):
            print_info("[editor] 撤销：结构生成")
        return bool(tool.undo())

    def select_tool(self, index):
        self.selected_tool = self.editor_tools[index]
        self.selected_layer = self.selected_tool.get('layer', None)


    def select_layer(self, layer_name):
        self.selected_layer = layer_name

    def _setup_tools(self):
        from src.editor.tools import editor_tools
        self.editor_tools = editor_tools

    def set_ui(self, ui_manager):
        self.ui = ui_manager
        self.ui.editor_ui.tools = self.editor_tools
        self.ui.editor_ui.layers = list(self.world.layers.names())

    def update(self, dt=1.0 / 60.0):
        # 更新悬停检测
        mouse_pos = pygame.mouse.get_pos()
        # 结构面板开着 / 正在拖拽放置时，先别做普通的悬停拾取与放置预览，
        # 免得两种预览叠在一起、或者误改到对象
        busy = self.structure_palette.open or self.structure_builder.active
        if not self.ui.editor_ui.property_panel.is_editing and not busy:
            self._update_hover(mouse_pos, dt)
        if busy:
            self.preview_obj = None
        else:
            self._update_preview(mouse_pos)
        self.wall_drag.update()               # 拖拽吸附框的丝滑动画
        self.structure_builder.update()
        self.structure_palette.update(dt, mouse_pos)
        self.collision_editor.update()
        # 同步选中的对象（从属性面板获取锁定对象）
        if self.ui and self.ui.editor_ui.property_panel.is_editing:
            self.collision_editor.set_selected_obj(self.ui.editor_ui.property_panel.locked_obj)
        else:
            self.collision_editor.set_selected_obj(self.hovered_obj)  # 或者 None

    def _candidates_at(self, world_pos):
        """点所在区块内的可编辑对象，按"最上层优先"排序。

        先按类型剔除编辑器不管的对象（`is_editor_object`：地形瓦片 Tiled 一律跳过），
        顺序规则：图层 order 越大越优先；同层内后注册的优先（后注册 = 后绘制 = 在上面）。
        旧实现里 hover 是同层"先注册优先"、delete 是"后注册优先"，两处并不一致；
        这里统一成后者（视觉上真正在上面那个）。

        规模：旧实现每帧/每次点击遍历世界上万个对象做矩形命中；现在只取空间网格里
        点所在区块的桶（几十个）。
        """
        order = {bucket.spec.name: idx for idx, bucket in enumerate(self.world.layers)}
        out = []
        for seq, obj in enumerate(self.world.objects_at(world_pos)):
            if not is_editor_object(obj):
                continue
            bucket = self.world.layers.find_bucket_of(obj)
            layer_order = order.get(bucket.spec.name, -1) if bucket is not None else -1
            out.append((layer_order, seq, obj))
        out.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [obj for _, _, obj in out]

    def _update_hover(self, mouse_pos, dt=1.0 / 60.0):
        world_pos = self.world.camera.screen_to_world(mouse_pos)
        panel = self.ui.editor_ui.property_panel
        self.hovered_obj = self._pick_object(world_pos, skip=panel.locked_obj)
        # 更新属性面板（update_obj 自身带指纹短路，同一对象不会每帧重建字段）
        if self.selected_tool and self.selected_tool.get('type') != 'delete' and self.hovered_obj:
            panel.update_obj(self.hovered_obj)
            panel.update_position(mouse_pos)
            panel.start_fade_in(dt)
        else:
            panel.start_fade_out(dt)

    def _pick_object(self, world_pos, skip=None):
        """鼠标世界坐标下最上层的可编辑对象（组合对象结构暂不支持编辑，跳过）。"""
        for obj in self._candidates_at(world_pos):
            if obj is skip or is_composite(obj):
                continue
            if obj.world_position.collidepoint(world_pos):
                return obj
        return None

    def _update_preview(self, mouse_pos):
        """根据当前工具和鼠标位置生成预览对象"""
        if (self.selected_tool is None or
            self.selected_tool.get('type') in ('delete', 'wall') or
            self.ui.editor_ui.property_panel.is_editing):
            self.preview_obj = None
            return

        world_pos = self.world.camera.screen_to_world(mouse_pos)
        if self.selected_tool == self.pre_selected_tool and self.preview_obj:
            self.preview_obj.world_position.center = world_pos
            return

        self.preview_obj = create_object_from_tool(
            self.selected_tool,
            world_pos,
        )
        # 更新pre_selected_tool
        if self.selected_tool != self.pre_selected_tool:
            self.pre_selected_tool = self.selected_tool

    def draw_preview(self, surface):
        """绘制预览对象（半透明）+ 临时拖拽吸附框"""
        if self.preview_obj:
            draw_placement_preview(surface, self.world.camera, self.preview_obj, alpha=128)
        self.wall_drag.draw(surface, self.world.camera)
        # 结构拖拽的吸附框（真正压在 UI 下面也无妨，UI 那层会盖住侧栏区域）
        self.structure_builder.draw(surface, self.world.camera)

    def handle_event(self, event, dt):
        # 属性面板正在输入时，键盘归它（不然在输入框里打 f/g 会被这里抢走），
        # 鼠标也不该被结构拖拽吃掉（否则点输入框会变成"放一个结构"）。
        panel = self.ui.editor_ui.property_panel if self.ui else None
        panel_editing = bool(panel is not None and panel.is_editing)

        # ---------- 0. 结构面板（F 呼出）----------
        # 放在最前面：面板开着时滚轮归列表（不再缩放镜头）、左键归选择，
        # 否则会点穿到世界里去。
        if (event.type == pygame.KEYDOWN and event.key == pygame.K_f
                and not panel_editing):
            self.structure_palette.toggle(pygame.mouse.get_pos(),
                                          self.world.screen_rect)
            return True
        if self.structure_palette.open and self.structure_palette.handle_event(event):
            return True

        # ---------- 0.1 结构拖拽生成（面板选完之后的放置阶段）----------
        if (self.structure_builder.active and not panel_editing
                and event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION,
                                   pygame.MOUSEBUTTONUP)):
            if self.structure_builder.handle_event(event, self.world.camera):
                return True

        # ---------- 0.2 临时调试工具：G 键开关横墙拖拽（F 已让给结构面板）----------
        if (event.type == pygame.KEYDOWN and event.key == pygame.K_g
                and not panel_editing):
            self.wall_drag.toggle()
            print_info(f"[editor] 横墙拖拽调试工具 'ON'（左键拖拽→松手生成横墙）")
            return True  # 消耗 G 键
        if event.type == pygame.KEYUP and event.key == pygame.K_g and not panel_editing:
            self.wall_drag.untoggle()
            print_info(f"[editor] 横墙拖拽调试工具 'OFF'")
            return True

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_z and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                # 统一撤销：结构生成器 + 横墙拖拽工具，谁最近撤谁
                if self.undo():
                    return True

        if self.wall_drag.enabled and event.type in (
                pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
            # 工具启用时左键事件归它管；右键等其它键不抢
            if event.type == pygame.MOUSEBUTTONUP and event.button != 1:
                pass
            elif self.wall_drag.handle_event(event, self.world.camera):
                return True

        # ---------- 1. 优先处理碰撞编辑器事件 ----------
        if self.collision_editor.active:
            if self.collision_editor.handle_event(event, self.world.camera):
                return True  # 事件被消耗，不再继续传递

        # ---------- 2. 原有事件处理 ----------
        if event.type == pygame.MOUSEBUTTONDOWN:
            # 用 event.pos 而不是 pygame.mouse.get_pos()：按键按下是"离散事件"，
            # 事件可能在队列里排了一会儿才被处理，此刻再轮询鼠标拿到的是"现在"的位置，
            # 而不是"当时点下去"的位置（在事件积压时会点偏）。
            mouse_pos = event.pos
            if event.button == 1:  # 左键
                # 排除左侧UI面板区域（防止与按钮冲突）
                if not pygame.Rect(0, 0, 350, 800).collidepoint(*mouse_pos):
                    # 墙壁工具特殊处理
                    if self.selected_tool and self.selected_tool.get('type') == 'wall':
                        self._handle_wall_click(mouse_pos)
                    else:
                        panel = self.ui.editor_ui.property_panel
                        # 情况1：鼠标悬停到有效对象 → 进入编辑模式（锁定该对象）
                        if self.hovered_obj is not None:
                            # 如果之前已编辑其他对象，先退出旧状态（立即退出，避免干扰）
                            if panel.is_editing:
                                panel.exit_edit_mode(0)
                            # 进入新对象的编辑状态
                            panel.is_editing = True
                            panel.alpha = 255
                            panel.show = True
                            panel.locked_obj = self.hovered_obj
                            panel.can_update_position = True
                            # 同时更新碰撞编辑器的选中对象
                            self.collision_editor.set_selected_obj(self.hovered_obj)
                        else:
                            # 情况2：点击空白处 → 如果当前正在编辑，则退出编辑模式
                            if panel.is_editing:
                                panel.exit_edit_mode(0)  # 立即退出
                                self.collision_editor.set_selected_obj(None)
                else:
                    pass  # 点击左侧UI面板不处理
            elif event.button == 3:  # 右键
                if not pygame.Rect(0, 0, 350, 800).collidepoint(*mouse_pos):
                    if not self.ui.editor_ui.property_panel.is_editing:
                        if self.selected_tool and self.selected_tool.get('type') != 'wall':
                            self._place_object(mouse_pos)
                        if self.selected_tool and self.selected_tool.get('type') == 'delete':
                            self._delete_object(mouse_pos)
                    if self.wall_edit_type:
                        self._finish_wall()
        elif event.type == pygame.KEYDOWN:
            # ---------- 新增快捷键：Ctrl+E 切换碰撞编辑器 ----------
            if event.mod & pygame.KMOD_CTRL and event.key == pygame.K_e:
                self.collision_editor.toggle_active()
                # 如果激活，自动选中当前属性面板锁定的对象
                if self.collision_editor.active:
                    panel = self.ui.editor_ui.property_panel
                    if panel.is_editing and panel.locked_obj:
                        self.collision_editor.set_selected_obj(panel.locked_obj)
                    else:
                        # 若没有锁定对象，则选中悬停对象
                        self.collision_editor.set_selected_obj(self.hovered_obj)
            elif event.key == K_F2:
                Command.execute("threaded(input_cmd)")
                return True  # 消耗事件

            # 原有快捷键
            if event.key == pygame.K_ESCAPE:
                self.ui.editor_ui.property_panel.exit_edit_mode(dt)
                self.collision_editor.set_selected_obj(None)
            if event.key == pygame.K_EQUALS or event.key == pygame.K_PLUS:
                self._zoom_camera(1.3)
            elif event.key == pygame.K_MINUS:
                self._zoom_camera(1 / 1.3)
        elif event.type == pygame.MOUSEMOTION:
            if self.wall_edit_type:
                mouse_pos = pygame.mouse.get_pos()
                self.temp_point = self.world.camera.screen_to_world(mouse_pos)
        elif event.type == pygame.MOUSEWHEEL:
            if event.y > 0:
                self._zoom_camera(1.3)
            elif event.y < 0:
                self._zoom_camera(1 / 1.3)

        # ---------- 属性面板输入处理 ----------
        # 面板"吃掉"事件（确认/取消/回车提交）时向外声明已消费，避免同一次点击
        # 再被别的编辑逻辑当成世界点击处理。
        if self.ui.editor_ui.property_panel.is_editing:
            if self.ui.editor_ui.property_panel.handle_input(
                    event, self.world.camera, self.world.layers, dt, self.world.physics):
                return True
        return False

    def _zoom_camera(self, factor):
        new_zoom = self.world.camera.zoom * factor
        self.world.camera.set_zoom(new_zoom, update_offset=True)

    def _place_object(self, mouse_pos):
        """放置实际对象（注册到世界）"""
        world_pos = self.world.camera.screen_to_world(mouse_pos)
        obj = create_object_from_tool(self.selected_tool, world_pos)
        if obj is None:
            return
        # 添加到对应图层
        eb = self.world.layers.get("effects")
        if hasattr(obj, 'effects_group'):
            obj.effects_group = eb.sprites if eb is not None else None
        layer_name = self.selected_layer
        self.world.add_game_object(obj, layer_name)

    def _delete_object(self, mouse_pos):
        world_pos = self.world.camera.screen_to_world(mouse_pos)
        # 顶层优先；组合对象(Structure)按“逐格叶子矩形”命中，命中即整墙一起删。
        # 候选同样从空间网格按点取（旧实现每次右键全表扫描一遍世界）。
        for obj in self._candidates_at(world_pos):
            if self._hits(obj, world_pos):
                self._remove_object(obj)
                return

    def _hits(self, obj, world_pos) -> bool:
        """命中判定：普通对象用外接矩形；组合对象(Structure)用其叶子各自矩形精确判定。"""
        if is_composite(obj):
            for leaf in leaf_sprites_of(obj):
                wp = getattr(leaf, "world_position", None)
                if wp is not None and wp.collidepoint(world_pos):
                    return True
            return False
        wp = getattr(obj, "world_position", None)
        return wp is not None and wp.collidepoint(world_pos)

    def _remove_object(self, obj):
        """删除一个对象/结构，并登记到删除日志（Ctrl+Z 原样放回原来那一层）。

        组合对象(Structure)整墙一次删净；普通对象走原逻辑 —— 两条路径不同，
        所以这里把"当时怎么删的"对应着写进恢复闭包，撤销时照着反做。
        """
        layer = self._layer_name_of(obj)
        was_player = (obj is self.world.active_player)

        if is_composite(obj):
            # 整墙：先从空间网格注销，再从所在层移除整个 Structure（叶子随其释放）
            self.world.unregister_object_from_grid(obj)
            self.world.layers.remove_object(obj)
        else:
            self.world.remove_game_object(obj)
            if was_player:
                self.world.active_player = None

        world = self.world

        def _restore():
            """放回原来那一层；删的是玩家的话把 active_player 也接回去。"""
            world.add_game_object(obj, layer)
            if was_player:
                world.active_player = obj
            return obj

        self.delete_log.record(f"删除 {type(obj).__name__}", _restore)

    def _handle_wall_click(self, mouse_pos):
        world_pos = self.world.camera.screen_to_world(mouse_pos)
        subtype = self.selected_tool.get('subtype')
        if subtype == 'polygon':
            self.wall_edit_type = 'polygon'
            self.wall_points.append(world_pos)
            self.temp_point = world_pos
        elif subtype == 'circle':
            if not self.wall_points:
                self.wall_edit_type = 'circle'
                self.wall_points.append(world_pos)
                self.temp_point = world_pos
        elif subtype == 'rectangle':
            if not self.wall_points:
                self.wall_edit_type = 'rectangle'
                self.wall_points.append(world_pos)
                self.temp_point = world_pos

    def _finish_wall(self):
        if not self.wall_points or not self.wall_edit_type:
            return
        if self.wall_edit_type == 'polygon' and len(self.wall_points) >= 3:
            wall = WallObject('polygon', self.wall_points.copy())
        elif self.wall_edit_type == 'circle' and len(self.wall_points) == 1 and self.temp_point:
            center = self.wall_points[0]
            radius = math.hypot(self.temp_point[0]-center[0], self.temp_point[1]-center[1])
            wall = WallObject('circle', (center, radius))
        elif self.wall_edit_type == 'rectangle' and len(self.wall_points) == 1 and self.temp_point:
            start = (min(self.wall_points[0][0], self.temp_point[0]), min(self.wall_points[0][1], self.temp_point[1]))
            width = abs(self.temp_point[0] - self.wall_points[0][0])
            height = abs(self.temp_point[1] - self.wall_points[0][1])
            wall = WallObject('rectangle', (start, (width, height)))
        else:
            self.wall_edit_type = None
            self.wall_points = []
            self.temp_point = None
            return
        self.world.add_game_object(wall, "collision")
        self.wall_edit_type = None
        self.wall_points = []
        self.temp_point = None