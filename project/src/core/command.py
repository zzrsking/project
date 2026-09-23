"""
command.py —— 基于 exec 的沙箱命令执行器（线程安全 + 可注册对象）

特性：
- 仅暴露已注册对象 + 安全内置函数，禁止 import/open/eval 等危险操作
- 支持 Python 全部语句（赋值、条件、循环、函数/类定义等）
- 每线程独立局部变量空间，保持跨命令状态
- 脚本内可使用 register() 动态注册新对象
"""

import builtins
import threading
import warnings
from typing import Any, Dict, Optional
import ast
import inspect

# 安全白名单内置函数（可根据需要增减）
SAFE_BUILTINS = {
    'abs': builtins.abs,
    'all': builtins.all,
    'any': builtins.any,
    'bin': builtins.bin,
    'bool': builtins.bool,
    'bytes': builtins.bytes,
    'chr': builtins.chr,
    'complex': builtins.complex,
    'dict': builtins.dict,
    'divmod': builtins.divmod,
    'enumerate': builtins.enumerate,
    'filter': builtins.filter,
    'float': builtins.float,
    'format': builtins.format,
    'frozenset': builtins.frozenset,
    'getattr': builtins.getattr,
    'hasattr': builtins.hasattr,
    'hash': builtins.hash,
    'hex': builtins.hex,
    'id': builtins.id,
    'int': builtins.int,
    'isinstance': builtins.isinstance,
    'issubclass': builtins.issubclass,
    'iter': builtins.iter,
    'len': builtins.len,
    'list': builtins.list,
    'map': builtins.map,
    'max': builtins.max,
    'min': builtins.min,
    'next': builtins.next,
    'oct': builtins.oct,
    'ord': builtins.ord,
    'pow': builtins.pow,
    'print': builtins.print,
    'range': builtins.range,
    'repr': builtins.repr,
    'reversed': builtins.reversed,
    'round': builtins.round,
    'set': builtins.set,
    'slice': builtins.slice,
    'sorted': builtins.sorted,
    'str': builtins.str,
    'sum': builtins.sum,
    'tuple': builtins.tuple,
    'type': builtins.type,
    'zip': builtins.zip,
    'Exception': Exception,
    'ValueError': ValueError,
    'TypeError': TypeError,
    'KeyError': KeyError,
    'True': True,
    'False': False,
    'None': None,
    'Ellipsis': Ellipsis,
}


class Command:
    """命令注册与执行器"""

    # 全局注册表
    _registry: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()
    _builtins_initialized = False

    # 每线程独立的局部变量空间（保持跨命令状态）
    _local_storage = threading.local()

    # --------------------------------------------------------------
    # 注册内置命令（一次性）
    # --------------------------------------------------------------
    @classmethod
    def _init_builtins(cls):
        if cls._builtins_initialized:
            return
        with cls._lock:
            if cls._builtins_initialized:  # 双重检查
                return
            # 内置类型转换函数直接使用安全白名单中的，也可以额外注册
            for name in ['int', 'str', 'float', 'bool', 'list', 'tuple',
                         'set', 'dict', 'complex', 'bytes', 'bytearray']:
                if name in SAFE_BUILTINS:
                    cls._register_unsafe(SAFE_BUILTINS[name], name=name, builtin=True)
            cls._builtins_initialized = True

    # --------------------------------------------------------------
    # 增删改查（线程安全）
    # --------------------------------------------------------------
    @classmethod
    def _register_unsafe(cls, obj, name=None, builtin=False):
        """不加锁的内部注册，调用方需持有锁。"""
        if name is None:
            name = obj.__name__ if hasattr(obj, '__name__') else str(obj)
        cls._registry[name] = {
            'obj': obj,
            'type': type(obj),
            'callable': callable(obj),
            'builtin': builtin,
        }

    @classmethod
    def register(cls, obj, name=None, builtin=False):
        """
        注册一个对象（函数、类、值等）。
        若 name 为 None，则尝试从调用栈中自动提取变量名（若唯一），否则 fallback。
        """
        if name is None:
            # 尝试从调用栈中获取变量名
            frame = inspect.currentframe()
            try:
                caller_frame = frame.f_back if frame else None
                if caller_frame:
                    # 获取调用者的局部变量和全局变量
                    local_vars = caller_frame.f_locals
                    global_vars = caller_frame.f_globals
                    # 找出所有指向 obj 的变量名
                    names = []
                    for var_name, var_value in local_vars.items():
                        if var_value is obj:
                            names.append(var_name)
                    for var_name, var_value in global_vars.items():
                        if var_value is obj and var_name not in names:
                            names.append(var_name)
                    if len(names) == 1:
                        name = names[0]
                    elif len(names) > 1:
                        warnings.warn(
                            f"Multiple variable names found for object {obj}, using first: {names[0]}"
                        )
                        name = names[0]
                    # 若 names 为空或大于1，name 保持 None，走 fallback
            finally:
                # 删除帧引用以避免循环引用
                del frame

        # 如果还没有获得 name，使用 fallback
        if name is None:
            name = obj.__name__ if hasattr(obj, '__name__') else str(obj)

        with cls._lock:
            cls._register_unsafe(obj, name, builtin)

    @classmethod
    def unregister(cls, name: str):
        with cls._lock:
            if name in cls._registry:
                del cls._registry[name]
            else:
                warnings.warn(f"Command '{name}' not found, nothing to unregister.")

    @classmethod
    def modify(cls, name: str, obj: Any):
        with cls._lock:
            if name not in cls._registry:
                raise KeyError(f"Command '{name}' is not registered.")
            builtin = cls._registry[name]['builtin']
            cls._registry[name] = {
                'obj': obj,
                'type': type(obj),
                'callable': callable(obj),
                'builtin': builtin,
            }

    @classmethod
    def query(cls, name: str) -> Optional[Dict[str, Any]]:
        with cls._lock:
            return cls._registry.get(name)

    @classmethod
    def list_commands(cls) -> list:
        with cls._lock:
            return list(cls._registry.keys())

    # --------------------------------------------------------------
    # 执行引擎（基于 exec）
    # --------------------------------------------------------------
    @classmethod
    def _get_thread_locals(cls) -> dict:
        """获取当前线程的局部变量字典，不存在则创建。"""
        if not hasattr(cls._local_storage, 'locals'):
            cls._local_storage.locals = {}
        return cls._local_storage.locals

    @classmethod
    def clear_thread_locals(cls):
        """清空当前线程的局部变量。"""
        quit()
        cls._local_storage.locals = {}

    @classmethod
    def execute(cls, script: str) -> None:
        """
        执行脚本。
        - 支持 Python 全部语句
        - 若脚本最后一行是表达式（无赋值、print 等），自动打印其结果
        """
        cls._init_builtins()
        with cls._lock:
            global_ns = {'__builtins__': SAFE_BUILTINS}
            for name, info in cls._registry.items():
                global_ns[name] = info['obj']
            global_ns['register'] = cls.register

        local_ns = cls._get_thread_locals()

        try:
            tree = ast.parse(script)
            # 提取最后一条表达式（如果存在）
            last_expr = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_expr = tree.body.pop()  # 从语句列表中移除
                # 将表达式部分编译为可执行的代码
                expr_code = compile(ast.Expression(last_expr.value), '<script>', 'eval')
            # 先执行前面的语句部分
            if tree.body:
                stmt_code = compile(ast.Module(body=tree.body, type_ignores=[]), '<script>', 'exec')
                exec(stmt_code, global_ns, local_ns)
            # 如果有最后的表达式，用 eval 求值并打印结果
            if last_expr:
                result = eval(expr_code, global_ns, local_ns)
                if result is not None:
                    print(result)
        except Exception as e:
            warnings.warn(f"Execution error: {e}")

    @classmethod
    def execute_async(cls, script: str) -> threading.Thread:
        """异步执行（返回 Thread 对象，可通过 .join() 等待）。"""
        t = threading.Thread(target=cls.execute, args=(script,))
        t.start()
        return t

    # --------------------------------------------------------------
    # 便捷方法：查看当前线程的局部变量
    # --------------------------------------------------------------
    @classmethod
    def get_locals(cls) -> dict:
        """返回当前线程的局部变量副本。"""
        return dict(cls._get_thread_locals())

    @classmethod
    def get_global_objects(cls) -> dict:
        """返回所有已注册对象的副本（不含内置）。"""
        with cls._lock:
            return {name: info['obj'] for name, info in cls._registry.items()}


import threading


def threaded(func, *args, **kwargs):
    th = threading.Thread(target=func, daemon=True, args=args, kwargs=kwargs)
    th.start()

def input_cmd():
    cmd = input(">>>")
    Command.execute(cmd)

def get_func(func_name):
    func = globals()[func_name]
    return func

def qkr(func_name):
    """quickly register - 显式传递 func_name 作为注册名"""
    Command.register(get_func(func_name), name=func_name)  # 修改：显式指定 name

import os
def run_script(script_name: str="test.txt"):
    """
        获取与当前文件同级的 'script' 文件夹下指定脚本的完整路径。

        :param script_name: str, 脚本文件名（如 'test.py'）
        :return: str, 该脚本的绝对路径
        """
    # 当前文件所在的目录（绝对路径）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 上一级目录（core 和 script 的共同父目录）
    parent_dir = os.path.dirname(current_dir)
    # script 文件夹的路径
    script_dir = os.path.join(parent_dir, 'script')
    # 拼接最终完整路径
    full_path = os.path.join(script_dir, script_name)
    with open(full_path) as f:
        Command.execute(str(f.read()))


Command.register(threaded)
Command.register(input_cmd)
Command.register(Command)
Command.register(get_func)
Command.register(qkr)
Command.register(run_script, name="r")
Command.register(globals)
Command.register(getattr)
Command.register(locals)
import pygame
Command.register(pygame)