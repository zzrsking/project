"""undo.py —— 编辑器撤销的"序号"来源。

为什么要一个全局序号：编辑器的撤销动作分散在多个工具里（结构生成器 structure_builder、
横墙拖拽调试工具 wall_drag_tool…），各自维护自己的 undo_list。Ctrl+Z 应该撤销
**最近发生的**那一件，而不是"某个固定工具的上一件"。

每个工具在记录一条可撤销项时取一个全局递增序号，Ctrl+Z 时比较各工具栈顶序号，
谁更大就撤谁 —— 无需把各工具的数据结构合并。
"""
import itertools

_counter = itertools.count(1)


def next_seq() -> int:
    """取一个全局递增的撤销序号（跨工具可比大小）。"""
    return next(_counter)


def newest(*tools):
    """从若干"有栈顶序号"的工具里挑出最近的那个。

    工具需要实现 `last_seq`（栈空返回 0）。返回 (seq, tool)；都没有可撤销项时返回 (0, None)。
    """
    best_seq, best_tool = 0, None
    for tool in tools:
        if tool is None:
            continue
        seq = int(getattr(tool, "last_seq", 0) or 0)
        if seq > best_seq:
            best_seq, best_tool = seq, tool
    return best_seq, best_tool
