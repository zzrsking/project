import pygame
import json
import base64
import io
from typing import Dict, List, Optional, Union, Callable
from src.core import resource_manager
import serializer


@serializer.register
class AnimationPlayer:
    def __init__(self, default_priority: int = -1):
        # ---------- 剪辑数据 ----------
        self.clips: Dict[str, List[pygame.Surface]] = {}
        self.priorities: Dict[str, int] = {}
        self.clip_speeds: Dict[str, float] = {}

        # ---------- 播放状态 ----------
        self.current_clip_name: Optional[str] = None
        self.current_priority: int = default_priority
        self.frames: List[pygame.Surface] = []
        self.frame_index: int = 0
        self.timer: float = 0.0
        self.is_playing: bool = False
        self.loop: bool = True
        self._on_finish_callback: Optional[Callable[[str], None]] = None

        # ---------- 当前帧 ----------
        self.current_frame: Optional[pygame.Surface] = None

    # ========== 注册剪辑 ==========
    def add_clip(self, name: str, frames: List[pygame.Surface], priority: int, fps: float = 20.0):
        """注册单个剪辑（不区分方向）"""
        if not frames:
            return
        self.clips[name] = frames
        self.priorities[name] = priority
        self.clip_speeds[name] = 1.0 / fps

    # ========== 播放控制 ==========
    def play(self, clip_name: str, loop: bool = True,
             priority: Optional[int] = None,
             callback: Optional[Callable[[str], None]] = None,
             force: bool = False):
        """
        请求播放剪辑，若新优先级 >= 当前优先级则打断播放。
        - clip_name: 剪辑名（如 "walk_right"）
        - loop: 是否循环
        - priority: 若为 None 则使用注册时的优先级
        - callback: 非循环播放结束回调
        """
        if clip_name not in self.clips:
            return

        real_priority = priority if priority is not None else self.priorities.get(clip_name, 0)

        if not force:
            if real_priority < self.current_priority:
                return

        if clip_name == self.current_clip_name and self.is_playing:
            return

        self.current_clip_name = clip_name
        self.current_priority = real_priority
        self.frames = self.clips[clip_name]
        self.frame_index = 0
        self.timer = 0.0
        self.loop = loop
        self.is_playing = True
        self._on_finish_callback = callback
        self._update_current_frame()

    def stop(self):
        self.is_playing = False

    def pause(self):
        self.is_playing = False

    def resume(self):
        if self.current_clip_name:
            self.is_playing = True

    def reset(self):
        """重置到第一帧并停止，优先级重置为默认（允许低优先级动画播放）"""
        self.frame_index = 0
        self.timer = 0.0
        self.is_playing = False
        self.current_priority = -1
        self._update_current_frame()

    # ========== 更新 ==========
    def update(self, delta_time: float):
        if not self.is_playing or not self.frames:
            return

        speed = self.clip_speeds.get(self.current_clip_name, 0.05)
        self.timer += delta_time

        if self.timer >= speed:
            self.timer = 0.0
            next_idx = self.frame_index + 1

            if next_idx >= len(self.frames):
                if self.loop:
                    self.frame_index = 0
                else:
                    self.frame_index = len(self.frames) - 1
                    self.is_playing = False
                    self._update_current_frame()
                    if self._on_finish_callback:
                        self._on_finish_callback(self.current_clip_name)
                    return
            else:
                self.frame_index = next_idx

            self._update_current_frame()

    def _update_current_frame(self):
        if self.frames and 0 <= self.frame_index < len(self.frames):
            self.current_frame = self.frames[self.frame_index]
        else:
            self.current_frame = None

    def get_current_frame(self) -> Optional[pygame.Surface]:
        return self.current_frame

    def get_all_frames(self) -> List[pygame.Surface]:
        """
        返回所有已注册剪辑中的所有帧（Surface 对象）。
        适用于批量资源管理、缓存清理或批量绘制等场景。
        """
        all_frames = []
        for frames in self.clips.values():
            if frames:
                all_frames.extend(frames)
        return all_frames

    @property
    def is_finished(self) -> bool:
        return (not self.is_playing) and self.frames and self.frame_index == len(self.frames) - 1

    @property
    def current_clip(self) -> Optional[str]:
        return self.current_clip_name

    def serialize(self):
        return {
            "current_clip": self.current_clip,
            "loop": self.loop,
            "playing": self.is_playing,          # 注意原代码中可能有 playing 属性，此处修正为 is_playing
            "frame_index": getattr(self, "frame_index", 0),
            "priority": getattr(self, "current_priority", 0),
        }

    @classmethod
    def deserialize(cls, data):
        ap = cls(default_priority=data.get("priority", 0))
        ap.current_clip_name = data.get("current_clip")
        ap.loop = data.get("loop", True)
        ap.is_playing = data.get("playing", False)
        ap.frame_index = data.get("frame_index", 0)
        return ap

    # ========== 新增三个方法：分组（frame group）管理 ==========
    def list_frame_groups(self) -> List[Dict]:
        """
        将所有注册的剪辑按前缀分组，返回每个分组的名称、所含剪辑列表、以及最高优先级。
        返回列表按优先级从高到低排序。
        """
        groups = {}
        for clip_name in self.clips.keys():
            # 提取前缀：以下划线分隔取第一部分，若无下划线则整体作为分组名
            if '_' in clip_name:
                group = clip_name.split('_')[0]
            else:
                group = clip_name

            if group not in groups:
                groups[group] = {
                    'name': group,
                    'clips': [],
                    'priority': -1
                }

            groups[group]['clips'].append(clip_name)
            pri = self.priorities.get(clip_name, 0)
            if pri > groups[group]['priority']:
                groups[group]['priority'] = pri

        # 按优先级降序排序
        sorted_groups = sorted(groups.values(), key=lambda x: x['priority'], reverse=True)
        return sorted_groups

    def get_frame_group_priority(self, group_name: str) -> int:
        """
        返回指定分组下所有剪辑的最高优先级，若分组不存在则返回 -1 并输出警告。
        """
        max_pri = -1
        found = False
        for clip_name in self.clips.keys():
            if '_' in clip_name:
                group = clip_name.split('_')[0]
            else:
                group = clip_name
            if group == group_name:
                pri = self.priorities.get(clip_name, 0)
                if pri > max_pri:
                    max_pri = pri
                found = True

        if not found:
            print(f"[WARNING] Frame group '{group_name}' not found.")
        return max_pri

    def set_frame_group_priority(self, group_name: str, priority: int):
        """
        将指定分组下的所有剪辑的优先级统一设置为 priority。
        如果该分组不存在，输出警告。
        """
        found = False
        for clip_name in self.clips.keys():
            if '_' in clip_name:
                group = clip_name.split('_')[0]
            else:
                group = clip_name
            if group == group_name:
                self.priorities[clip_name] = priority
                found = True

        if not found:
            print(f"[WARNING] Frame group '{group_name}' not found. No priority changed.")