# src/dialogue/tree.py
import serializer
@serializer.register
class DialogueNode:
    def __init__(self, node_id: str, text: str, options: list = None):
        if not isinstance(node_id, str):
            raise TypeError("node_id 必须是字符串类型")
        if not text:
            raise ValueError("对话文本不能为空")

        self.id = node_id
        self.text = text
        self.options = options or []

        # 如果节点无选项，自动添加一个“结束”选项（兼容默认行为）
        if not self.options:
            self.options.append(DialogueOption(
                text="离开",
                next_node="exit",
                condition_id=None,  # 无条件
                effects={"end_dialogue": True}
            ))


@serializer.register
class DialogueOption:
    def __init__(self, text: str, next_node: str, condition_id: str = None, effects: dict = None):
        """
        :param text: 选项文本
        :param next_node: 跳转目标节点ID
        :param condition_id: 条件ID（对应注册表中的函数），None 表示无条件
        :param effects: 效果字典（如 {"add_item": ["药水", 1]}）
        """
        self.text = text
        self.next_node = next_node
        self.condition_id = condition_id  # 旧 conditions 字典被彻底移除
        self.effects = effects or {}