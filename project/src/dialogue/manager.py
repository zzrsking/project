from src.utils.helpers import print_info
from src.dialogue.condition_registry import ConditionRegistry
from src.dialogue.tree import DialogueNode, DialogueOption
import serializer


def default_condition_handler(condition_id, player, game):
    """解析并检查条件，支持 item:物品ID:数量 和 level:等级"""
    if condition_id.startswith("item:"):
        parts = condition_id.split(":")
        if len(parts) == 3:
            item_id = parts[1]
            required = int(parts[2])
            return player.inventory.get(item_id, 0) >= required
    elif condition_id.startswith("level:"):
        parts = condition_id.split(":")
        if len(parts) == 2:
            required = int(parts[1])
            return player.level >= required
    return False

ConditionRegistry.set_default_handler(default_condition_handler)

@serializer.register
class DialogueManager:
    def __init__(self, player, game=None):
        self.player = player
        self.game = game
        self.dialogue_tree = None
        self.current_node = None
        self.active = False

        self._state = "IDLE"          # IDLE | PENDING
        self._pending_option = None
        self._pending_index = -1

    def start_dialogue(self, dialogue_tree, entry_node_id: str = "start"):
        if not dialogue_tree:
            print_info("错误：对话树为空")
            return

        self.active = True
        self.dialogue_tree = dialogue_tree   # 必须是字典 {node_id: node_data}
        self.current_node = self._get_node(entry_node_id)
        self._state = "IDLE"
        self._pending_option = None

        if not self.current_node:
            print(f"错误：找不到起始节点 '{entry_node_id}'，可用节点: {list(self.dialogue_tree.keys())}")
            self.end_dialogue()

    def _get_node(self, node_id: str) -> DialogueNode:
        """
        从对话树中获取节点，兼容两种数据格式：
        - 字典（旧格式）或
        - 已经是 DialogueNode 实例
        """
        node_data = self.dialogue_tree.get(node_id)
        if not node_data:
            return None

        # 如果已经是 DialogueNode，直接返回
        if isinstance(node_data, DialogueNode):
            return node_data

        # 如果是字典，转换为 DialogueNode
        if isinstance(node_data, dict):
            options = []
            for opt_data in node_data.get("options", []):
                cond_id = opt_data.get("condition_id")
                if cond_id is not None and not isinstance(cond_id, str):
                    print_info(f"[警告] 选项 '{opt_data['text']}' 的 condition_id 不是字符串，已忽略")
                    cond_id = None
                options.append(DialogueOption(
                    text=opt_data["text"],
                    next_node=opt_data["next_node"],
                    condition_id=cond_id,
                    effects=opt_data.get("effects", {})
                ))
            return DialogueNode(node_id, node_data["text"], options)

        return None

    def select_option(self, option_index: int):
        if not self._validate_selection(option_index):
            return

        option = self.current_node.options[option_index]
        self._pending_option = option
        self._pending_index = option_index
        self._state = "PENDING"

    def get_pending_requirement(self) -> dict:
        if self._state != "PENDING" or not self._pending_option:
            return {}
        cond_id = self._pending_option.condition_id
        if not cond_id:
            return {}
        return ConditionRegistry.get_details(cond_id)

    def confirm_pending(self) -> bool:
        if self._state != "PENDING" or not self._pending_option:
            print_info("[警告] 没有待确认的选项")
            return False

        option = self._pending_option
        cond_id = option.condition_id

        is_met = True
        if cond_id:
            is_met = ConditionRegistry.check(cond_id, self.player, self.game)

        if not is_met:
            self._state = "IDLE"
            print_info("[飘字提示] 条件不足，请检查所需材料")
            return False

        self._apply_effects(option.effects)

        if option.effects.get("end_dialogue", False):
            self.end_dialogue()
            return True

        self._transition_node(option.next_node)
        self._state = "IDLE"
        self._pending_option = None
        return True

    def cancel_pending(self):
        self._state = "IDLE"
        self._pending_option = None
        self._pending_index = -1

    def end_dialogue(self):
        self.active = False
        self.dialogue_tree = None
        self.current_node = None
        self._state = "IDLE"
        self._pending_option = None
        self._pending_index = -1
        print_info("对话已正常结束")

    def _validate_selection(self, option_index):
        if not self.active:
            print_info("警告：对话未激活")
            return False
        if not self.current_node:
            print_info("错误：当前对话节点为空")
            return False
        if not (0 <= option_index < len(self.current_node.options)):
            print_info(f"无效选项索引: {option_index}")
            return False
        return True

    def _transition_node(self, next_node_id: str):
        next_node = self._get_node(next_node_id)
        if not next_node or not next_node.options:
            self.end_dialogue()
            return
        self.current_node = next_node
        print_info(f"进入对话节点: {self.current_node.id}")

    def _apply_effects(self, effects: dict):
        if not effects:
            return
        for effect_type, params in effects.items():
            try:
                if effect_type == "add_item":
                    item_id, quantity = params if isinstance(params, tuple) else (params, 1)
                    from src.item.item import Item
                    item = Item(item_id, item_id, None, quantity=quantity)
                    self.player.inventory.add_item(item)
                    print_info(f"获得物品: {item_id}x{quantity}")

                elif effect_type == "trigger_event":
                    if self.game and hasattr(self.game, 'trigger_event'):
                        self.game.trigger_event(params)
                    else:
                        print_info(f"警告：游戏引用未设置或缺少 trigger_event 方法，无法触发事件 '{params}'")

                elif effect_type == "modify_stat":
                    stat, value = params
                    current = getattr(self.player, stat, 0)
                    setattr(self.player, stat, current + value)
                    print_info(f"{stat} 已变更: {value:+}")

                elif effect_type == "complete_quest":
                    if hasattr(self.player, 'complete_quest'):
                        self.player.complete_quest(params)
                    else:
                        print_info(f"警告：player 没有 complete_quest 方法，任务 '{params}' 未完成")

                elif effect_type == "end_dialogue":
                    pass

            except Exception as e:
                print_info(f"效果执行失败 [{effect_type}]: {str(e)}")