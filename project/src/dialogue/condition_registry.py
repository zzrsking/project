# src/dialogue/condition_registry.py
from typing import Dict, Any, Callable, Optional

from src.utils.helpers import print_info


class ConditionRegistry:
    """条件注册表 - 所有判定逻辑集中于此"""
    _conditions: Dict[str, Callable] = {}
    _details: Dict[str, Dict] = {}
    _default_handler = None

    @classmethod
    def set_default_handler(cls, handler):
        """设置默认处理器，用于处理未显式注册的 condition_id"""
        cls._default_handler = handler

    @classmethod
    def register(cls, condition_id: str, details: Optional[Dict] = None):
        """
        装饰器：注册条件判定函数
        :param condition_id: 唯一标识符（对应JSON中的值）
        :param details: 用于UI弹窗展示的材料详情，格式如 {"items": {"火把": 1}, "level": 5}
        """
        def decorator(func: Callable) -> Callable:
            cls._conditions[condition_id] = func
            if details is not None:
                cls._details[condition_id] = details
            return func
        return decorator

    @classmethod
    def check(cls, condition_id, player, game):
        func = cls._conditions.get(condition_id)
        if func:
            return func(player, game)
        if cls._default_handler:
            return cls._default_handler(condition_id, player, game)
        print_info(f"[警告] 未注册的条件ID: {condition_id}")
        return False

    @classmethod
    def get_details(cls, condition_id):
        if not isinstance(condition_id, str):
            return {}
        if condition_id.startswith("item:"):
            parts = condition_id.split(":")
            if len(parts) == 3:
                return {"items": {parts[1]: int(parts[2])}}
        elif condition_id.startswith("level:"):
            parts = condition_id.split(":")
            if len(parts) == 2:
                return {"level": int(parts[1])}
        return cls._details.get(condition_id, {})

    @classmethod
    def is_registered(cls, condition_id: str) -> bool:
        return condition_id in cls._conditions