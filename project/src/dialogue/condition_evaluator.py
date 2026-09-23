import re
import operator as op
from typing import Any, Dict, List, Union

class ConditionEvaluator:
    """安全的条件表达式评估器（支持路径访问和方法调用）
    "eq"(==)、"ne"(!=)、"gt"(>)、"lt"(<)、"ge"(>=)、"le"(<=)
    """

    # 支持的操作符映射
    _operators = {
        "eq": op.eq,
        "ne": op.ne,
        "gt": op.gt,
        "lt": op.lt,
        "ge": op.ge,
        "le": op.le,
    }

    def __init__(self, context: Dict[str, Any]):
        """
        context 是一个字典，用于注入可访问的根对象。
        例如：{"player": player, "world": world, "game": engine}
        """
        self.context = context

    def evaluate(self, condition: Union[Dict, List, Any]) -> bool:
        """公共入口：评估条件，如果是简单布尔值直接返回"""
        if condition is None or condition == {}:
            return True
        if isinstance(condition, bool):
            return condition
        if not isinstance(condition, dict):
            raise TypeError(f"条件必须是字典或布尔值, 得到 {type(condition)}")

        cond_type = condition.get("type")

        if cond_type == "compare":
            return self._eval_compare(condition)
        elif cond_type == "method":
            return self._eval_method(condition)
        elif cond_type == "and":
            return self._eval_and(condition)
        elif cond_type == "or":
            return self._eval_or(condition)
        elif cond_type == "not":
            return self._eval_not(condition)
        else:
            # 为了向后兼容旧的硬编码方式（item, level, quest）
            return self._eval_legacy(condition)

    # ---------- 核心路径解析 ----------
    def _resolve_path(self, path: str) -> Any:
        """
        解析点号路径和字典/列表索引。
        示例:
            "player.level"                -> player.level
            "world.npcs['老猎人'].is_alive" -> 先获取 world.npcs，再取 ['老猎人']，再取 is_alive
            "player.inventory.items[0]"   -> 列表索引
        """
        if not path:
            raise ValueError("路径不能为空")

        # 分割为 tokens（处理括号和点号）
        tokens = self._tokenize_path(path)
        current = None

        for i, token in enumerate(tokens):
            if i == 0:
                # 第一个 token 必须从 context 中获取
                if token not in self.context:
                    raise KeyError(f"根对象 '{token}' 未在上下文中注册")
                current = self.context[token]
            else:
                if current is None:
                    raise ValueError(f"在路径 '{path}' 中，{token} 之前的对象为 None")

                # 处理字典/列表索引，例如 ['key'] 或 [0]
                if token.startswith('[') and token.endswith(']'):
                    index = token[1:-1]
                    # 去除引号
                    if (index.startswith('"') and index.endswith('"')) or \
                       (index.startswith("'") and index.endswith("'")):
                        index = index[1:-1]
                    else:
                        # 尝试转为整数（列表索引）
                        try:
                            index = int(index)
                        except ValueError:
                            pass
                    current = current[index]
                else:
                    # 普通属性访问
                    current = getattr(current, token, None)
                    if current is None:
                        # 尝试字典键访问（防御）
                        if isinstance(current, dict) and token in current:
                            current = current[token]

        return current

    def _tokenize_path(self, path: str) -> List[str]:
        """将路径拆分为 token 列表，处理 ['key'] 和 [0]"""
        # 使用正则提取：匹配点号分隔的单词，或方括号内的内容
        pattern = r"(\w+)|(\['[^']*'\]|\[\"[^\"]*\"\]|\[\d+\])"
        matches = re.findall(pattern, path)
        tokens = []
        for m in matches:
            # m 是一个元组，其中一个组有值
            token = m[0] if m[0] else m[1]
            tokens.append(token)
        return tokens

    # ---------- 比较评估 ----------
    def _eval_compare(self, cond: Dict) -> bool:
        path = cond.get("path")
        op_str = cond.get("op")
        value = cond.get("value")

        if not path or op_str not in self._operators:
            raise ValueError(f"无效的比较条件: {cond}")

        actual = self._resolve_path(path)
        return self._operators[op_str](actual, value)

    # ---------- 方法调用评估 ----------
    def _eval_method(self, cond: Dict) -> bool:
        path = cond.get("path")
        method_name = cond.get("method")
        args = cond.get("args", [])
        op_str = cond.get("op")
        value = cond.get("value")

        if not path or not method_name:
            raise ValueError(f"无效的方法条件: {cond}")

        obj = self._resolve_path(path)
        if not hasattr(obj, method_name):
            raise AttributeError(f"对象 {obj} 没有方法 {method_name}")

        method = getattr(obj, method_name)
        # 调用方法（假设 args 是列表）
        result = method(*args) if isinstance(args, list) else method(args)
        return self._operators[op_str](result, value)

    # ---------- 逻辑组合 ----------
    def _eval_and(self, cond: Dict) -> bool:
        for sub_cond in cond.get("conditions", []):
            if not self.evaluate(sub_cond):
                return False
        return True

    def _eval_or(self, cond: Dict) -> bool:
        for sub_cond in cond.get("conditions", []):
            if self.evaluate(sub_cond):
                return True
        return False

    def _eval_not(self, cond: Dict) -> bool:
        sub_cond = cond.get("condition")
        return not self.evaluate(sub_cond)

    # ---------- 向后兼容（旧版硬编码） ----------
    def _eval_legacy(self, cond: Dict) -> bool:
        """
        支持旧的 {"item": ["火把", 1]}、{"level": 5}、{"quest": ["寻宝", "完成"]}
        这样你的旧 JSON 依然有效
        """
        if "item" in cond:
            item_id, required_count = cond["item"]
            # 假设 player 在上下文中，且 player.inventory 是一个 dict
            player = self.context.get("player")
            if not player:
                return False
            # 如果 inventory 是字典：player.inventory.get(item_id, 0)
            if hasattr(player.inventory, 'get'):  # 字典
                actual = player.inventory.get(item_id, 0)
            elif hasattr(player.inventory, 'has'):  # 有 has 方法
                actual = 1 if player.inventory.has(item_id) else 0
            else:
                actual = 0
            return actual >= required_count

        elif "level" in cond:
            player = self.context.get("player")
            return player and player.level >= cond["level"]

        elif "quest" in cond:
            # 简单实现：假设 quests 是一个 dict
            quest_id, required_state = cond["quest"]
            player = self.context.get("player")
            if not player or not hasattr(player, 'quests'):
                return False
            actual_state = player.quests.get(quest_id, {}).get('state')
            return actual_state == required_state

        return True