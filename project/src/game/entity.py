import pygame
import math
import random
from pygame import Vector2

from src.animation.animation_player import AnimationPlayer
from src.utils.vector import Position
from src.core.resource_manager import (get_resource_path, load_image,
                                       load_procedural_surface, get_surface_by_id,
                                       flip_surface, scale_surface)
from src.ui.elements.inventory import InventoryInterface
from src.dialogue.manager import DialogueManager
from src.utils import helpers as h
from settings import physics
import serializer


class Genetic(pygame.sprite.Sprite):
    # 默认不可碰撞：装饰/瓦片精灵（Tiled / TablePlantTile / 各种 Tile）本就不该进物理引擎。
    # 显式给类属性，是为了保证 obj.is_collidable 永远有值 —— 少一个属性就会在
    # "读档时逐个加入对象"的循环里抛 AttributeError 并**中断整次读档**。
    is_collidable = False

    def __init__(self, position: tuple[int, int], image: pygame.Surface):
        super().__init__()
        self.floor = 0
        self.image = image
        self.rect = self.image.get_rect(center=position)
        self.world_position = Position(0, 0, 0, 0)
        self.world_position.size = self.rect.size
        self.world_position.center = position
        self.in_active_block = False
        self.is_locked = False

    @property
    def centery(self) -> float|int:
        return self.world_position.centery

    @centery.setter
    def centery(self, value):
        self.world_position.centery = value


@serializer.register
class Tiled(Genetic):
    def __init__(self, position: tuple[int, int], image: pygame.Surface):
        super().__init__(position, image)



@serializer.register
class GameObject(Genetic):
    def __init__(self, image, center=(0, 0), is_collidable=False,
                 interact_type="none", set_visible=True):

        super().__init__(center, pygame.Surface((0, 0)))


        self.lock_colors = {
            "enemy": (255, 0, 0),
            "friend": (0, 255, 0),
            "neutral": (255, 255, 0)
        }
        self.max_speed = 800
        self.accel = 5000
        self.friction = 3500
        self.is_lockable = True
        self.lock_marker_color = (255, 255, 0)
        self.in_active_block = set_visible

        if isinstance(image, list) and all(isinstance(s, pygame.Surface) for s in image):
            self.frames = image
            self.original_image = image[0]
            self.image = image[0]
        elif isinstance(image, pygame.Surface):
            self.frames = [image]
            self.original_image = image
            self.image = image
        else:
            raise TypeError("image 必须是 pygame.Surface 或 List[pygame.Surface]")


        self.rect = self.image.get_rect(center=center)
        self.img_angle = 0
        self.world_position = Position(*self.rect.topleft, *self.rect.size)
        self.is_collidable = is_collidable
        self.collision_shapes = []
        self.properties = {"name": "New Object"}

        self.animation_speed = 0.05
        self.frame_index = 0
        self.animation_timer = 0.0

        self.anim_player = AnimationPlayer(default_priority=-1)
        self.animation_children = []

        self.bind_camera = None
        self.lock_distance = 300
        self.lock_angle_threshold = 45
        self.current_lock_target = None
        self.toward = pygame.math.Vector2(0, 0)

        self.interact_type = interact_type
        self.interact_range = 300

    def update_animation(self, delta_time):
        self.anim_player.update(delta_time)
        frame = self.anim_player.get_current_frame()
        if frame is not None:
            self.image = frame
            self.rect.size = self.image.get_size()
            self.world_position.width = self.rect.width
            self.world_position.height = self.rect.height

        for child in self.animation_children:
            if hasattr(child, 'update_animation'):
                child.update_animation(delta_time)
            elif hasattr(child, 'update'):
                child.update(delta_time)

    def update_visual(self, dt, camera_offset, zoom, all_objects=None):
        """视觉更新（默认空实现，子类可覆盖）"""
        pass

    # ---------- 锁定目标的公共簿记 ----------
    def clear_lock_target(self):
        """放弃当前锁定目标（把 is_locked 收回自己手上）。"""
        target = self.current_lock_target
        if target is not None:
            target.is_locked = False
        self.current_lock_target = None

    def _set_lock_target(self, obj):
        """换锁定目标：只动旧目标/新目标两处标志，不再全表复位 is_locked。"""
        previous = self.current_lock_target
        if previous is obj:
            return False
        if previous is not None:
            previous.is_locked = False
        self.current_lock_target = obj
        if obj is not None:
            obj.is_locked = True
        return True

    @staticmethod
    def _angle_deg(dx, dy):
        """与 pygame `Vector2().angle_to(offset)` 完全等价（零向量 -> 0）。"""
        return math.degrees(math.atan2(dy, dx))

    def update_lock_target(self, all_objects):
        """基类锁定：按朝向挑"角度最近、其次最近"的一个目标。

        性能：不再每帧把全体对象 is_locked 复位（O(N) 写操作），
        也不再为每个候选分配 Vector2；距离用平方比较，只有入选者才算开方。
        """
        toward = self.toward
        if toward.length_squared() == 0:
            self.clear_lock_target()
            return

        move_angle = self._angle_deg(toward.x, toward.y)
        max_distance = self.lock_distance
        max_distance_sq = max_distance * max_distance
        self_x = self.world_position.x
        self_y = self.world_position.y

        best_key = None
        best_obj = None
        for obj in all_objects:
            if obj is self or not getattr(obj, "is_lockable", False):
                continue
            wp = obj.world_position
            dx = wp.x - self_x
            dy = wp.y - self_y
            dist_sq = dx * dx + dy * dy
            if dist_sq > max_distance_sq:
                continue
            angle_diff = abs(self._angle_deg(dx, dy) - move_angle) % 360
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            key = (angle_diff, dist_sq)
            if best_key is None or key < best_key:
                best_key = key
                best_obj = obj

        self._set_lock_target(best_obj)

    def update_rect(self, camera_offset, zoom=1.0):
        self.rect.x = (self.world_position.x - camera_offset[0]) * zoom
        self.rect.y = (self.world_position.y - camera_offset[1]) * zoom
        self.rect.width = self.world_position.width * zoom
        self.rect.height = self.world_position.height * zoom

    def update_angle(self, angle):
        self.img_angle = angle
        self._update_rotate()

    def _update_rotate(self):
        self.image = pygame.transform.rotate(self.original_image, self.img_angle)

    def temp_image_alpha(self, alpha):
        temp_img = self.image.copy()
        temp_img.set_alpha(alpha)
        return temp_img

    def interact(self, obj):
        pass

    def start_continuous_effect(self):
        """启动持续特效（由World在玩家锁定时调用）"""
        pass

    def stop_continuous_effect(self):
        """停止持续特效（由World在取消锁定时调用）"""
        pass

    def draw_continuous_effect(self, camera, surface):
        """绘制持续特效（每帧由World调用）"""
        pass


@serializer.register
class ProceduralObject(GameObject):
    def __init__(self, image=None, center=(0, 0), is_collidable=False,
                 interact_type="none", set_visible=True,
                 procedural_type=None, procedural_params=()):
        super().__init__(image, center, is_collidable, interact_type, set_visible)
        self.procedural_type = procedural_type
        self.procedural_params = procedural_params


@serializer.register
class LoadedImageObject(GameObject):
    pass


# ---------- Player ----------
@serializer.register
class Player(LoadedImageObject):
    def __init__(self, right_frames, left_frames, center=(0, 0), screen_rect=(1200, 800)):
        self._move_dir = None
        if not right_frames or not left_frames:
            raise ValueError("right_frames and left_frames cannot be empty")

        super().__init__(image=right_frames[0], center=center, interact_type="player")

        self.anim_player.add_clip("idle_right", [right_frames[0]], priority=0, fps=1)
        walk_right = right_frames[1:] if len(right_frames) > 1 else [right_frames[0]]
        self.anim_player.add_clip("walk_right", walk_right, priority=1, fps=20)

        self.anim_player.add_clip("idle_left", [left_frames[0]], priority=0, fps=1)
        walk_left = left_frames[1:] if len(left_frames) > 1 else [left_frames[0]]
        self.anim_player.add_clip("walk_left", walk_left, priority=1, fps=20)

        self._right_frames = right_frames
        self._left_frames = left_frames

        self.current_direction = 'right'
        self.moving = False
        self.screen_rect = screen_rect

        self.manual_locking = False
        self.candidate_targets = []
        self.current_candidate_index = 0
        self.health = 1
        self.max_health = 100
        self.attack_range = 100
        self.attack_power = 10
        self.attack_cooldown = 1.0
        self.last_attack_time = 0
        self.level = 0
        self.is_collidable = True
        self.inventory = InventoryInterface(self.screen_rect)
        self.dialogue_manager = DialogueManager(self)
        self.current_item = None
        self.velocity = Vector2(0, 0)
        self.collision_radius = self.rect.width / 2

        self.play_idle()

    def set_movement_params(self, max_speed=None, accel=None, friction=None):
        """供外部环境（地面、道具等）调整移动参数"""
        if max_speed is not None:
            self.max_speed = max_speed
        if accel is not None:
            self.accel = accel
        if friction is not None:
            self.friction = friction

    def apply_movement(self, direction: Vector2, dt: float, physics_world):
        """
        由World每帧调用，传入归一化方向向量和物理世界引用。
        内部使用自身的 accel、friction、max_speed 更新物理体速度。
        """
        self._move_dir = direction
        if self._move_dir.length() != 0:

            self._move_dir = self._move_dir.normalize()
        if direction.x != 0 or direction.y != 0:
            # 玩家重新操作方向 -> 解除手动锁定，回到"面向即锁定"的自动模式
            self.manual_locking = False
        body = physics_world.get_body(self)
        if body is None:
            return

        vx, vy = body.velocity
        # 水平
        if direction.x != 0:
            vx += direction.x * self.accel * dt
            if abs(vx) > self.max_speed:
                vx = direction.x * self.max_speed
        else:
            if abs(vx) > 0:
                friction = self.friction * dt
                if abs(vx) < friction:
                    vx = 0
                else:
                    vx -= (vx / abs(vx)) * friction
        # 垂直
        if direction.y != 0:
            vy += direction.y * self.accel * dt
            if abs(vy) > self.max_speed:
                vy = direction.y * self.max_speed
        else:
            if abs(vy) > 0:
                friction = self.friction * dt
                if abs(vy) < friction:
                    vy = 0
                else:
                    vy -= (vy / abs(vy)) * friction
        body.velocity = (vx, vy)

    # ---------- 覆盖基类视觉更新（整合动画、方向、矩形、锁定） ----------
    def update_visual(self, dt, camera_offset, zoom, all_objects=None):
        """由World在每帧物理同步后调用"""
        # 1. 更新动画帧（基类已有 update_animation）
        self.update_animation(dt)

        # 2. 根据移动方向切换行走/待机动画
        if self._move_dir.length() > 0:
            if self._move_dir.x < 0:
                self.set_direction("left")
            elif self._move_dir.x > 0:
                self.set_direction("right")
            self.play_walk()
        else:
            self.play_idle()

        # 3. 更新屏幕矩形（摄像机变换）
        self.update_rect(camera_offset, zoom)

        # 4. 锁定目标更新（需要外部传入所有对象）
        if all_objects is not None:
            self.update_lock_target(all_objects)

    # ========== 动画控制 ==========
    def play_idle(self):
        clip = f"idle_{self.current_direction}"
        self.anim_player.play(clip, loop=True, priority=1)

    def play_walk(self):
        clip = f"walk_{self.current_direction}"
        self.anim_player.play(clip, loop=True, priority=1)

    def play_hit(self, callback=None):
        clip = f"hit_{self.current_direction}"
        self.anim_player.play(clip, loop=False, priority=2, callback=callback)

    def set_direction(self, direction):
        if direction not in ('left', 'right'):
            return
        self.current_direction = direction
        current_clip = self.anim_player.current_clip
        if current_clip:
            if current_clip.startswith("idle_"):
                self.play_idle()
            elif current_clip.startswith("walk_"):
                self.play_walk()

    # ========== 原有方法 ==========
    def update_lock_target(self, all_objects):
        """玩家锁定：角度权重 0.6 + 距离权重 0.4 评分，取最优。

        与原实现决策等价（同样的评分公式、同样的稳定排序取首个），但：
          · 朝向不为零时不再每帧为每个候选构造 Vector2 / 反复重算 move_angle；
          · 距离先用平方比较，只有入选候选才算距离开方；
          · 不变量只在"目标换了"时写，避免静止时也逐帧刷 is_locked。
        """
        toward = self.toward
        if toward.length_squared() == 0 and not self.manual_locking:
            self.clear_lock_target()
            self.candidate_targets = []
            self.current_candidate_index = 0
            return

        max_distance = self.lock_distance
        max_distance_sq = max_distance * max_distance
        max_angle = 180.0
        self_x = self.world_position.x
        self_y = self.world_position.y

        if toward.length_squared() > 0:
            move_angle = self._angle_deg(toward.x, toward.y)
        else:
            move_angle = None       # 手动锁定：只看距离

        candidates = []
        append = candidates.append
        for obj in all_objects:
            if obj is self or not getattr(obj, "is_lockable", False):
                continue
            wp = obj.world_position
            dx = wp.x - self_x
            dy = wp.y - self_y
            dist_sq = dx * dx + dy * dy
            if dist_sq > max_distance_sq:
                continue
            distance = math.sqrt(dist_sq)
            if move_angle is None:
                angle_diff = 0.0
            else:
                angle_diff = abs(self._angle_deg(dx, dy) - move_angle) % 360
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
            score = (angle_diff / max_angle) * 0.6 + (distance / max_distance) * 0.4
            append((score, obj))

        if not candidates:
            self.candidate_targets = []
            self.current_candidate_index = 0
            self._set_lock_target(None)
            return

        candidates.sort(key=lambda c: c[0])
        self.candidate_targets = [c[1] for c in candidates]
        self.current_candidate_index = 0
        self._set_lock_target(self.candidate_targets[0])

    def force_lock_nearest(self, candidates=None):
        """手动锁定最近的目标（不受朝向限制）。Q 键用。

        :param candidates: 候选列表；不传则退回上一次锁定扫描得到的候选序。
        """
        pool = self.candidate_targets if candidates is None else candidates
        if not pool:
            self.clear_lock_target()
            return None

        max_distance = self.lock_distance
        max_distance_sq = max_distance * max_distance
        self_x = self.world_position.x
        self_y = self.world_position.y

        best_obj = None
        best_sq = None
        fallback_obj = None
        fallback_sq = None
        for obj in pool:
            if obj is self or not getattr(obj, "is_lockable", False):
                continue
            wp = obj.world_position
            dx = wp.x - self_x
            dy = wp.y - self_y
            dist_sq = dx * dx + dy * dy
            if fallback_sq is None or dist_sq < fallback_sq:
                fallback_sq = dist_sq
                fallback_obj = obj
            if dist_sq <= max_distance_sq and (best_sq is None or dist_sq < best_sq):
                best_sq = dist_sq
                best_obj = obj

        # 锁定距离内没有目标时，退化为"最近的那个"（手动锁定不该什么都不做）
        chosen = best_obj if best_obj is not None else fallback_obj
        self._set_lock_target(chosen)
        if chosen is not None and chosen not in self.candidate_targets:
            self.candidate_targets = [chosen]
            self.current_candidate_index = 0
        return chosen

    def take_damage(self, amount):
        self.health = max(0, self.health - amount)
        print(f"受到 {amount} 点伤害！剩余生命：{self.health}")
        if self.health <= 0:
            self.die()

    def die(self):
        print("玩家死亡！")
        self.kill()

    def add_item(self, item):
        self.inventory.add_item(item)

    def use_item(self, item_slot):
        item = self.inventory.slots[item_slot]['item']
        if item.id == "health_potion":
            self.health = min(self.max_health, self.health + 30)
            self.inventory.remove_item(item_slot)

    def complete_quest(self, quest_id):
        print(f"完成任务: {quest_id}")

    @classmethod
    def from_legacy(cls, image_path, centerx, centery, width, height, frame_number):
        frames = load_image(
            get_resource_path(image_path),
            scale=(width, height),
            frame_count=frame_number
        )
        return cls(frames, (centerx, centery))


# ---------- NPC ----------
class NPC(LoadedImageObject):
    def __init__(self, frames, center=(0, 0), dialogue_tree=None):
        self._dialogue_tree = dialogue_tree
        super().__init__(image=frames[0], center=center, interact_type="npc")
        self.is_collidable = True
        self.dialogue_tree = dialogue_tree

    def interact(self, player):
        if player.dialogue_manager.active:
            return
        player.dialogue_manager.start_dialogue(self.dialogue_tree)


# ---------- WallObject ----------
@serializer.register
class WallObject(ProceduralObject):
    def __init__(self, wall_type, points, center=(0, 0), is_collidable=True):
        if wall_type == 'rectangle':
            pos, size = points
            pos = (float(pos[0]), float(pos[1])) if not isinstance(pos, tuple) else pos
            size = (float(size[0]), float(size[1])) if not isinstance(size, tuple) else size
            points = (pos, size)
        elif wall_type == 'circle':
            center_pt, radius = points
            center_pt = (float(center_pt[0]), float(center_pt[1]))
            points = (center_pt, float(radius))
        elif wall_type == 'polygon':
            points = tuple((float(p[0]), float(p[1])) for p in points)
        self._points = points
        self._wall_type = wall_type
        self._center = center
        self._is_collidable = is_collidable

        if wall_type == 'circle':
            center_pt, radius = points
            surface = load_procedural_surface('wall_circle', (center_pt, radius))
        elif wall_type == 'rectangle':
            pos, size = points
            surface = load_procedural_surface('wall_rect', (pos, size))
        elif wall_type == 'polygon':
            surface = load_procedural_surface('wall_polygon', points)
        else:
            raise ValueError(f"不支持的wall_type类型{wall_type}")

        super().__init__(image=surface, center=center, is_collidable=is_collidable, procedural_type="wall_"+wall_type, procedural_params=self._points)

        self.wall_type = wall_type
        self.points = points
        self.ELASTICITY = 0
        self.is_lockable = False

        if wall_type == 'circle':
            center_pt, radius = points
            self.rect = pygame.Rect(center_pt[0]-radius, center_pt[1]-radius, 2*radius, 2*radius)
        elif wall_type == 'rectangle':
            pos, size = points
            self.rect = pygame.Rect(pos[0], pos[1], size[0], size[1])
        elif wall_type == 'polygon':
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            self.rect = pygame.Rect(min_x, min_y, max_x-min_x, max_y-min_y)
        self.world_position = Position(self.rect.x, self.rect.y, self.rect.width, self.rect.height)

        self.properties["name"] = f"{wall_type.capitalize()} Wall"
        self.properties["wall_type"] = wall_type

    def kill(self):
        super().kill()

    def point_inside(self, point):
        if self.wall_type == 'polygon':
            return self._point_in_polygon(point)
        elif self.wall_type == 'circle':
            center, radius = self.points
            distance = math.sqrt((point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2)
            return distance <= radius
        elif self.wall_type == 'rectangle':
            return self.rect.collidepoint(point)
        return False

    def _point_in_polygon(self, point):
        x, y = point
        n = len(self.points)
        inside = False
        p1x, p1y = self.points[0]
        for i in range(n + 1):
            p2x, p2y = self.points[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def distance_to_point(self, point):
        if self.wall_type == 'polygon':
            return self._distance_to_polygon(point)
        elif self.wall_type == 'circle':
            center, radius = self.points
            distance = math.sqrt((point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2)
            return distance
        elif self.wall_type == 'rectangle':
            dx = max(self.rect.left - point[0], 0, point[0] - self.rect.right)
            dy = max(self.rect.top - point[1], 0, point[1] - self.rect.bottom)
            return math.sqrt(dx * dx + dy * dy)
        return float('inf')

    def _distance_to_polygon(self, point):
        min_dist = float('inf')
        n = len(self.points)
        for i in range(n):
            p1 = self.points[i]
            p2 = self.points[(i + 1) % n]
            min_dist = min(min_dist, self._point_to_line_distance(point, p1, p2))
        return min_dist

    @staticmethod
    def _point_to_line_distance(point, line_p1, line_p2):
        line_len = math.sqrt((line_p2[0] - line_p1[0]) ** 2 + (line_p2[1] - line_p1[1]) ** 2)
        if line_len == 0:
            return math.sqrt((point[0] - line_p1[0]) ** 2 + (point[1] - line_p1[1]) ** 2)
        t = max(0, min(1, ((point[0] - line_p1[0]) * (line_p2[0] - line_p1[0]) +
                           (point[1] - line_p1[1]) * (line_p2[1] - line_p1[1])) / (line_len ** 2)))
        proj_x = line_p1[0] + t * (line_p2[0] - line_p1[0])
        proj_y = line_p1[1] + t * (line_p2[1] - line_p1[1])
        return math.sqrt((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2)


# ---------- Portal ----------
@serializer.register
class Portal(ProceduralObject):
    def __init__(self, surface, center=(0, 0)):
        super().__init__(image=surface, center=center, interact_type="portal", procedural_type="portal", procedural_params=())
        self._center = center
        self.properties["name"] = "Portal"
        self.interact_range = 100
        self.target_portal = None
        self.target_portal_id = None
        self.effects_group = None
        self.is_collidable = False
        self._effect_active = False
        self._particle_timer = 0.0

    def kill(self):
        super().kill()

    def set_effects_group(self, group):
        self.effects_group = group

    def spawn_particles(self, target_portal):
        if not self.effects_group or not target_portal:
            return
        source = self.world_position.center
        target = target_portal.world_position.center
        for _ in range(20):
            particle = Particle(source, target, color=(255, 255, 0), size=4, lifetime=1.5)
            self.effects_group.add(particle)

    def receive(self, target):
        self.target_portal = target

    def start_continuous_effect(self):
        self._effect_active = True

    def stop_continuous_effect(self):
        self._effect_active = False
        self._particle_timer = 0.0

    def draw_continuous_effect(self, camera, surface, dt=1.0 / 60.0):
        """锁定时每帧绘制"传送门连线 + 少量粒子"。

        旧实现在这里既按计时器生成粒子、又无条件调 spawn_particles()（一次 20 颗），
        只要被调用就会每帧灌进特效层且永不回收；现在粒子按 dt 计时、定量生成。
        """
        if not self._effect_active or not self.target_portal:
            return
        # 绘制连线（使用相机坐标转换）
        start = camera.world_to_screen(self.world_position.center)
        end = camera.world_to_screen(self.target_portal.world_position.center)
        pygame.draw.line(surface, (255, 255, 0), start, end, 2)

        if not self.effects_group:
            return
        self._particle_timer += dt
        if self._particle_timer < 0.1:
            return
        self._particle_timer -= 0.1
        for _ in range(2):      # 0.1s 两颗：稳定而不会滚雪球
            self.effects_group.add(Particle(
                self.world_position.center,
                self.target_portal.world_position.center,
                color=(255, 255, 0),
                size=3,
                lifetime=0.8
            ))

    def get_effect_data(self, effect_type='line'):
        """
        返回特效数据，供 EffectManager 使用。
        :param effect_type: 'line' 或 'particle_stream'
        """
        if not self.target_portal:
            return None
        if effect_type == 'line':
            return {
                'type': 'line',
                'data': {
                    'start': self.world_position.center,
                    'end': self.target_portal.world_position.center,
                    'color': (255, 255, 0),
                    'width': 2
                }
            }
        elif effect_type == 'particle_stream':
            return {
                'type': 'particle_stream',
                'data': {
                    'start': self.world_position.center,
                    'end': self.target_portal.world_position.center,
                    'color': (255, 255, 0),
                    'speed': 200,
                    'spawn_rate': 0.05
                }
            }
        return None


@serializer.register
class sunshiyi(NPC):
    def __init__(self, image, center=(0, 0)):
        super().__init__(frames=[image], center=center)
        self.is_collidable = True

class HorizontalWoodWallTiledTop(Genetic):
    def __init__(self, center=(0, 0)):
        surf = get_surface_by_id("terrain/graphics/environment/House_40")
        super().__init__(center, surf)
        self.in_active_block = True

class HorizontalWoodWallTiledMiddle(Genetic):
    def __init__(self, center=(0, 0)):
        surf = get_surface_by_id("terrain/graphics/environment/House_41")
        super().__init__(center, surf)
        self.in_active_block = True

class HorizontalWoodWallTiledBottom(Genetic):
    def __init__(self, center=(0, 0)):
        surf = get_surface_by_id("terrain/graphics/environment/House_42")
        super().__init__(center, surf)
        self.in_active_block = True

class BrickFloorTile(Genetic):
    """砖块地板（BrickFloor）瓦片。整块地板只用这一种 —— 砖纹 House_17。"""
    def __init__(self, center=(0, 0)):
        surf = get_surface_by_id("terrain/graphics/environment/House_17")
        super().__init__(center, surf)
        self.in_active_block = True


# ---------- 竖木墙（VerticalWoodWall）纹理 ----------
# 竖墙尺寸语义：宽(Y) 恒为 1（厚度约定，不在网格里体现）；逻辑尺寸 = 长(X) × 高(Z)。
# 与横木墙同一张 House.png。
#   · 默认按“左侧竖墙”样式构造（竖墙在横墙左侧、向右连横墙）：
#       中部行 = 44（原样），直接接底 = 43（原样），
#       顶部 = 33 —— 33 原图面向“右侧竖墙”，故用于左侧时需水平翻转一次。
#   · 46 = 专给 1×1 单层的结束纹理，现在不做单层，不用。
#   · 连接后顶部：向右连(House_8) / 向左连(House_10)。
#
# 换图职责划分（2026 定稿，见 CALLBACK_SURFACE.md §7）：
# - **判定在墙里**：VerticalWall.wall_texture_plan() 算出整墙每格的 (帧号, 翻转)；
# - **贴图在精灵里**：精灵只实现 apply_frame(帧号, 翻转)，并缓存翻转面，不自己算该贴哪张。
# 这样同一件事不会在墙和精灵两处各写一遍（曾经因此掩盖过一个 _tail_crossed 笔误）。

# House 瓦片加载与翻转缓存
_VWALL_FLIP_CACHE = {}

def _vw_surface(house_id: str, flip_h: bool = False):
    surf = get_surface_by_id(f"terrain/graphics/environment/House_{house_id}")
    if surf is None:
        return None
    if not flip_h:
        return surf
    key = (id(surf), house_id)
    if key not in _VWALL_FLIP_CACHE:
        # 走 resource_manager 的 flip_surface：翻转结果会登记"配方"，
        # 于是存档里只写"哪张图 + 翻转"，而不是这块像素
        _VWALL_FLIP_CACHE[key] = flip_surface(surf, True, False)
    return _VWALL_FLIP_CACHE[key]



# ---- 竖墙纹理判定已集中到 vertical_texture.vertical_cell_frame ----
from src.core.build_game.vertical_texture import vertical_cell_frame  # noqa: E402


def _apply_vertical_frame(sprite, cell_type, *, wall=None, orientation="left",
                          head_right=False, head_left=False,
                          tail_connected=False, tail_right=False, tail_cross=False, head_cross=False):
    head_run = False
    if wall is not None:
        orientation = getattr(wall, "orientation", orientation)
        # 从 wall 读取 cross 状态（若未传入，则从 wall 取）
        head_cross = head_cross or getattr(wall, "_head_cross", False)
        tail_cross = tail_cross or getattr(wall, "_tail_cross", False)
        # 头格是否"上面还有同一堵墙"（走廊串联 / 走廊接房子）：是则改跑通纹
        head_run = bool(getattr(wall, "_head_run", False))
        # 也可读取连接状态
        if hasattr(wall, "_head_connected"):
            head_right = wall._head_right if wall._head_connected else False
            head_left = (not wall._head_right) if wall._head_connected else False
        if hasattr(wall, "_tail_connected"):
            # 尾段"连着但共线"（wall._tail_run）时按直通处理：不画转角纹（24/15/26/7）
            tail_connected = (wall._tail_connected
                              and not bool(getattr(wall, "_tail_run", False)))
            tail_right = wall._tail_connect_right if tail_connected else False

    # 墙顶补厚度那一段（头格下面 height-1 格）：只有中段需要，问墙要
    cross_run = False
    if wall is not None and cell_type == "top_run":
        probe = getattr(wall, "cell_in_cross_span", None)
        if callable(probe):
            try:
                cross_run = bool(probe(sprite))
            except Exception:      # noqa: BLE001
                cross_run = False

    house_id, flip = vertical_cell_frame(
        cell_type,
        orientation=orientation,
        head_right=head_right, head_left=head_left,
        tail_connected=tail_connected,
        tail_right=tail_right,
        tail_cross=tail_cross,
        head_cross=head_cross,
        head_run=head_run,
        cross_run=cross_run
    )
    surf = _vw_surface(house_id, flip_h=flip)
    if surf is not None:
        sprite._base_surf = surf
        sprite.image = surf


class _VWallFrameSprite(Genetic):
    """竖墙精灵的公共基类：换图的**唯一入口** `apply_frame(帧号, 翻转)`。

    判定逻辑（哪一格该贴哪张图）集中在 `VerticalWall.wall_texture_plan()`；
    精灵只负责"把给定帧贴到自己身上"，并暴露 `_base_surf` 供观测与侧脸合成。
    这样"墙"和"精灵"各管一件事，不再互相读对方的状态（见 CALLBACK_SURFACE.md §7）。

    兼容：旧调用点若还在调 `on_wall_dock(wall, ...)`，就按墙的当前 plan 取本格帧号。
    """
    def __init__(self, center=(0, 0), surf=None):
        if surf is None:
            surf = pygame.Surface((64, 64))
        # _base_surf 必须**无条件**存在：它是"当前原图"的权威引用，
        # 观测（tex_of）与侧脸合成（VerticalWall._decorate_leaf）都直接读它。
        self._base_surf = surf
        super().__init__(center, surf)
        self.in_active_block = True

    def apply_frame(self, frame_id, flip: bool = False) -> None:
        """按帧号 + 翻转换图；取不到图时保持原样（不炸）。"""
        surf = _vw_surface(str(frame_id), flip_h=bool(flip))
        if surf is not None:
            self._base_surf = surf
            self.image = surf

    @property
    def frame_id(self) -> str:
        """当前贴的是哪一号帧（观测用；带 * 表示翻转）。"""
        base = getattr(self, "_base_surf", None)
        if base is None:
            return "-"
        for i in range(1, 90):
            if base is _vw_surface(str(i)):
                return str(i)
            if base is _vw_surface(str(i), True):
                return f"{i}*"
        return "?"

    def on_wall_dock(self, wall, *args, **kwargs):
        """兼容入口：按墙当前 plan 里本格那一行的帧号换图。"""
        cell = wall._cell_of_leaf(self) if hasattr(wall, "_cell_of_leaf") else None
        if cell is None:
            return
        for gy, _ct, (fid, flip) in wall.wall_texture_plan():
            if gy == int(cell[1]):
                self.apply_frame(fid, flip)
                return


class VerticalWoodWallTiledTopTail(_VWallFrameSprite):
    """
    竖墙·顶段底部精灵（可连口）。半格纹理(32/64)：
      - 本墙直通/未折向时跟随头（左墙=16 / 右墙=18）跑穿；
      - 本墙自己的口被接上时，按“尾连接方向 vs 头方向 是否同向”选图：
           左墙 同向→24 / 反向→15；右墙 同向→26 / 反向→7。
    """
    def __init__(self, center=(0, 0)):
        super().__init__(center, _vw_surface(16))            # 默认左墙直通

    def _set(self, surf):
        if surf is not None:
            self._base_surf = surf
            self.image = surf


class VerticalWoodWallTiledTopRun(_VWallFrameSprite):
    """
    竖墙顶部·上下连通段精灵（16/18）。
    它不是打头的顶、也不是拐弯/收尾——它上方/下方还有墙面连过来，
    所以贴的是能上下连通的 16(左墙) / 18(右墙) 纹。
    """
    def __init__(self, center=(0, 0)):
        super().__init__(center, _vw_surface(16))            # 默认左墙样式(16)


class VerticalWoodWallTiledTop(_VWallFrameSprite):
    """竖墙顶部精灵（打头/拐弯格）：帧号由墙的 plan 决定（33 / 8 / 10 / 22）。"""
    def __init__(self, center=(0, 0)):
        super().__init__(center, _vw_surface(33, flip_h=True))   # 默认左墙样式(33翻转)

    def _set_base(self, surf):
        if surf is not None:
            self._base_surf = surf
            self.image = surf


class VerticalWoodWallTiledMiddle(_VWallFrameSprite):
    """竖墙中部行精灵（44 系；跨半格时 41/55/49 系）。"""
    def __init__(self, center=(0, 0)):
        super().__init__(center, _vw_surface(44))


class VerticalWoodWallTiledBottom(_VWallFrameSprite):
    """竖墙底部收尾精灵（43 系；跨半格时 42/63/50 系）。"""
    def __init__(self, center=(0, 0)):
        super().__init__(center, _vw_surface(43))


# ---------- 桌子（Table）纹理 ----------
# House Decoration 贴图集：assets/terrain/Tilesets/House Decoration.tsx 自己声明
#   tilewidth=64 tileheight=64 columns=9 tilecount=54
# 即 9 列 × 6 行 = 54 张 64×64 瓦片（图片 576×384）。setup.py 必须按 9×6 切，
# 否则编号整体错位（曾经按 8×6 切成 72×64，切出来的 30 号根本不是桌子）。
_DECORATION_COLS = 9
_DECORATION_ROWS = 6


def _decoration_surface(idx: int):
    """取 House Decoration 贴图集第 idx 号瓦片。

    取不到时**显式报错**而不是让 None 一路流到 Genetic.__init__ 里变成
    “'NoneType' object has no attribute 'get_rect'” 那种查不出所以然的崩溃
    —— 这类“资源没注册好但报错在别处”正是要防的 bug。
    """
    surf = get_surface_by_id(f"terrain/graphics/environment/House Decoration_{idx}")
    if surf is None:
        raise RuntimeError(
            f"House Decoration 贴图集第 {idx} 号瓦片未注册。请检查 setup.py 是否已 "
            f"load_tileset('terrain\\\\graphics\\\\environment\\\\House Decoration.png', "
            f"splitter=split_grid({_DECORATION_COLS}, {_DECORATION_ROWS}))"
            f"（{_DECORATION_COLS}×{_DECORATION_ROWS} 与 Tiled 的 .tsx 声明一致）。")
    return surf


@serializer.register
class TableTopTile(Genetic):
    """桌面（`table` 层）瓦片：矩形木桌板面 = House Decoration 第 30 号。

    30 号 = 9 列布局下的第 3 行第 3 列（0 起），即那张带桌腿的矩形木桌。
    """
    def __init__(self, center=(0, 0)):
        super().__init__(center, _decoration_surface(30))
        self.in_active_block = True


@serializer.register
class DecorationTile(Genetic):
    """House Decoration 贴图集里任意一号瓦片（桌面/地面装饰通用）。

    子类声明 `DECOR_INDEX = 编号`（按 Tiled 的 9 列布局，与 setup.py 一致）；
    桌面上的物件通常比一格小，可用 `DECOR_SIZE` 指定缩放后的像素尺寸。
    """
    DECOR_INDEX = 0
    DECOR_SIZE = None      # None = 用原图尺寸（64×64）

    def __init__(self, center=(0, 0)):
        surf = _decoration_surface(type(self).DECOR_INDEX)
        if type(self).DECOR_SIZE:
            # 走 resource_manager 的 scale_surface：登记配方 -> 存档只写缩放参数
            surf = scale_surface(surf, type(self).DECOR_SIZE)
        super().__init__(center, surf)
        self.in_active_block = True


@serializer.register
class TablePlantTile(DecorationTile):
    """摆在桌面上的盆栽（House Decoration 第 3 号）—— `object` 层的示例物体。

    3 号 = 9 列布局下第 0 行第 3 列，即那盆金盆绿植。
    缩到 40×40：桌上物件比一格(64)小才像“摆在桌面上”，而不是把整张桌盖住。
    """
    DECOR_INDEX = 3
    DECOR_SIZE = (40, 40)


# ---------- Particle ----------
class Particle(pygame.sprite.Sprite):
    def __init__(self, pos, target_pos, color=(255, 255, 0), size=4, lifetime=1.0):
        super().__init__()
        self.pos = pygame.math.Vector2(pos)
        self.target = pygame.math.Vector2(target_pos)
        self.direction = (self.target - self.pos).normalize()
        self.speed = random.uniform(100, 200)
        self.lifetime = lifetime
        self.max_lifetime = lifetime
        self.color = color
        self.size = size
        self.offset = pygame.math.Vector2(random.uniform(-10, 10), random.uniform(-10, 10))
        self.pos += self.offset
        self.direction = (self.target - self.pos).normalize()
        self.image = pygame.Surface((size*2, size*2), pygame.SRCALPHA)
        pygame.draw.circle(self.image, self.color, (size, size), size)
        self.rect = self.image.get_rect(center=self.pos)

    def update(self, dt):
        self.pos += self.direction * self.speed * dt
        self.lifetime -= dt
        alpha = int(255 * (self.lifetime / self.max_lifetime))
        self.image.set_alpha(alpha)
        self.rect.center = self.pos
        if self.lifetime <= 0:
            self.kill()