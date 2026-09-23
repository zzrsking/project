import pygame
from pygame import Vector2
import random
from src.game.camera import Camera   # 用于坐标转换

class EffectManager:
    """
    统一管理持续特效（连线、粒子流等）。
    特效数据由实体提供，管理器负责更新（生命周期、粒子运动）和绘制。
    """
    def __init__(self):
        self.active_effects = []   # 每个元素是字典，包含类型、数据、生命周期等
        self.particle_group = pygame.sprite.Group()  # 可选的粒子精灵组

    def add_effect(self, effect_type, data, duration=None):
        """
        添加一个特效。
        :param effect_type: 'line' 或 'particle_stream'
        :param data: 字典，包含 start, end, color, width, speed, etc.
        :param duration: 持续秒数，None 表示无限（直到手动移除）
        :return: 特效的引用（用于后续移除）
        """
        effect = {
            'type': effect_type,
            'data': data.copy(),
            'duration': duration,
            'elapsed': 0.0,
            'alive': True
        }
        self.active_effects.append(effect)
        return effect

    def remove_effect(self, effect_ref):
        """标记特效为无效，在下一帧更新时清除"""
        if effect_ref in self.active_effects:
            effect_ref['alive'] = False

    def update(self, dt):
        """更新所有特效，清理过期/死亡的"""
        # 处理粒子流：生成新粒子（如果类型为 particle_stream）
        for eff in self.active_effects[:]:
            if not eff['alive']:
                self.active_effects.remove(eff)
                continue
            if eff['duration'] is not None:
                eff['elapsed'] += dt
                if eff['elapsed'] >= eff['duration']:
                    self.active_effects.remove(eff)
                    continue
            # 如果是粒子流，每帧生成粒子
            if eff['type'] == 'particle_stream':
                self._update_particle_stream(eff, dt)
        # 更新粒子精灵组
        self.particle_group.update(dt)

    def _update_particle_stream(self, effect, dt):
        """生成粒子流粒子，添加到 particle_group"""
        data = effect['data']
        start = data.get('start')
        end = data.get('end')
        color = data.get('color', (255, 255, 0))
        speed = data.get('speed', 200)
        spawn_rate = data.get('spawn_rate', 0.05)  # 秒
        # 这里简化：每帧生成固定数量
        if random.random() < spawn_rate * 10:  # 简单的概率控制
            # 创建粒子，利用现有 Particle 类
            from src.game.entity import Particle
            particle = Particle(
                pos=start,
                target_pos=end,
                color=color,
                size=random.randint(3, 6),
                lifetime=random.uniform(0.5, 1.5)
            )
            self.particle_group.add(particle)

    def draw(self, surface, camera):
        """
        绘制所有特效（使用 Camera 转换世界坐标到屏幕坐标）
        """
        # 绘制连线
        for eff in self.active_effects:
            if not eff['alive']:
                continue
            if eff['type'] == 'line':
                data = eff['data']
                start_world = data['start']
                end_world = data['end']
                start_screen = camera.world_to_screen(start_world)
                end_screen = camera.world_to_screen(end_world)
                color = data.get('color', (255, 255, 0))
                width = data.get('width', 2)
                pygame.draw.line(surface, color, start_screen, end_screen, width)
            # 其他类型（如光晕）可扩展

        # 绘制粒子（需要转换坐标）
        for particle in self.particle_group:
            # 假设 Particle 的 pos 是世界坐标，rect.center 已更新
            screen_pos = camera.world_to_screen(particle.pos)
            particle.rect.center = screen_pos
            surface.blit(particle.image, particle.rect)

    def clear_all(self):
        """清除所有特效和粒子"""
        self.active_effects.clear()
        self.particle_group.empty()