import threading
import time
import warnings
from typing import Callable, Optional, Dict, Tuple, List, Any
from enum import Enum
from src.core.command import Command


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"


_queue: List[Dict] = []
_lock = threading.RLock()
_counter = 0


def default_callback(task_id, result):
    print(f"\n--- 系统自动回调触发 ---")
    print(f"任务 [{task_id}] 已经结束")
    if isinstance(result, Exception):
        print(f"任务执行出错了: {result}")
    else:
        print(f"任务的执行结果是: {result}")
    print("------------------------\n")


def push(
    func: Callable,
    args: Tuple = (),
    kwargs: Optional[Dict] = None,
    async_mode: bool = False,
    on_complete: Optional[Callable[[str, Any], None]] = default_callback,
    task_id: Optional[str] = None,
    name: Optional[str] = None,
    repeat: int = 0,
    on_final_complete: Optional[Callable[[str, Any], None]] = None,
) -> str:
    global _counter
    if not callable(func):
        raise TypeError("func 必须是可调用对象")
    kwargs = kwargs or {}
    task_id = task_id or f"task-{time.time_ns()}-{_counter}"
    _counter += 1
    name = name or task_id

    task = {
        "id": task_id,
        "name": name,
        "func": func,
        "args": args,
        "kwargs": kwargs,
        "async": async_mode,
        "on_complete": on_complete,
        "on_final_complete": on_final_complete,
        "status": TaskStatus.PENDING,
        "repeat": repeat,
        "stop_requested": False,
        "final_called": False,
    }
    with _lock:
        _queue.append(task)
    return task_id


def push_recurring(
    func: Callable,
    args: Tuple = (),
    kwargs: Optional[Dict] = None,
    async_mode: bool = False,
    repeat: Optional[int] = None,
    on_final_complete: Optional[Callable[[str, Any], None]] = None,
    name: Optional[str] = None,
) -> str:
    return push(
        func=func,
        args=args,
        kwargs=kwargs,
        async_mode=async_mode,
        on_complete=None,
        repeat=repeat,
        on_final_complete=on_final_complete,
        name=name,
    )


def execute(task_name: Optional[str] = None, count: int = 1) -> int:
    if count <= 0 and task_name is None:
        return 0

    if task_name is not None:
        target = None
        with _lock:
            for t in _queue:
                if t["status"] == TaskStatus.PENDING and t.get("name") == task_name:
                    target = t
                    t["status"] = TaskStatus.RUNNING
                    break
        if target is None:
            return 0
        return _run_single_task(target)

    tasks_to_run = []
    with _lock:
        pending = [t for t in _queue if t["status"] == TaskStatus.PENDING]
        selected = pending[:count]
        for t in selected:
            t["status"] = TaskStatus.RUNNING
            tasks_to_run.append(t)

    if not tasks_to_run:
        return 0

    executed = 0
    for task in tasks_to_run:
        executed += _run_single_task(task)
    return executed


def _run_single_task(task: Dict) -> int:
    def handle_finish(t, result, error):
        with _lock:
            repeat = t.get("repeat", 0)
            stop_requested = t.get("stop_requested", False)

            if stop_requested:
                t["status"] = TaskStatus.FINISHED
                if not t.get("final_called", False):
                    t["final_called"] = True
                    cb = t.get("on_final_complete") or t.get("on_complete")
                    if cb:
                        try:
                            cb(t["id"], "任务被手动停止")
                        except Exception as e:
                            warnings.warn(f"停止回调执行出错: {e}")
                return

            if repeat is None:
                t["status"] = TaskStatus.PENDING
            elif repeat > 0:
                t["repeat"] = repeat - 1
                if t["repeat"] > 0:
                    t["status"] = TaskStatus.PENDING
                else:
                    t["status"] = TaskStatus.FINISHED
                    if not t.get("final_called", False):
                        t["final_called"] = True
                        cb = t.get("on_final_complete") or t.get("on_complete")
                        if cb:
                            try:
                                cb(t["id"], error if error else result)
                            except Exception as e:
                                warnings.warn(f"最终回调执行出错: {e}")
            else:
                t["status"] = TaskStatus.FINISHED
                if not t.get("final_called", False):
                    t["final_called"] = True
                    cb = t.get("on_complete") or t.get("on_final_complete")
                    if cb:
                        try:
                            cb(t["id"], error if error else result)
                        except Exception as e:
                            warnings.warn(f"回调执行出错: {e}")

    if task["async"]:
        def runner(t=task):
            result = None
            error = None
            try:
                result = t["func"](*t["args"], **t["kwargs"])
            except Exception as e:
                error = e
                warnings.warn(f"任务 {t['id']} 异步执行出错: {e}")
            finally:
                handle_finish(t, result, error)
        threading.Thread(target=runner, daemon=True).start()
        return 1
    else:
        result = None
        error = None
        try:
            result = task["func"](*task["args"], **task["kwargs"])
        except Exception as e:
            error = e
            warnings.warn(f"任务 {task['id']} 同步执行出错: {e}")
        finally:
            handle_finish(task, result, error)
        return 1


def stop(task_id: str) -> bool:
    with _lock:
        for t in _queue:
            if t["id"] == task_id:
                if t["status"] == TaskStatus.FINISHED:
                    return False
                t["stop_requested"] = True
                if t["status"] == TaskStatus.PENDING:
                    t["status"] = TaskStatus.FINISHED
                    if not t.get("final_called", False):
                        t["final_called"] = True
                        cb = t.get("on_final_complete") or t.get("on_complete")
                        if cb:
                            try:
                                cb(task_id, "任务被手动停止")
                            except Exception as e:
                                warnings.warn(f"停止回调执行出错: {e}")
                return True
    return False


def delete(
    task_id: Optional[str] = None,
    task_name: Optional[str] = None,
    force: bool = False
) -> int:
    if task_id is None and task_name is None:
        return 0

    removed = 0
    with _lock:
        if task_id is not None:
            for i, t in enumerate(_queue):
                if t["id"] == task_id:
                    if force or t["status"] != TaskStatus.RUNNING:
                        del _queue[i]
                        removed += 1
                    break
        else:
            new_queue = []
            for t in _queue:
                if t.get("name") == task_name and (force or t["status"] != TaskStatus.RUNNING):
                    removed += 1
                else:
                    new_queue.append(t)
            _queue[:] = new_queue
    return removed


def clear(force: bool = False) -> None:
    with _lock:
        if force:
            _queue.clear()
        else:
            _queue[:] = [t for t in _queue if t["status"] == TaskStatus.RUNNING]


def check() -> Dict[str, Any]:
    with _lock:
        tasks = []
        for t in _queue:
            copy = t.copy()
            copy["status"] = copy["status"].value
            copy.pop("func", None)
            copy.pop("stop_requested", None)
            copy.pop("final_called", None)
            tasks.append(copy)
        return {
            "length": len(tasks),
            "tasks": tasks,
        }


def size() -> int:
    with _lock:
        return len(_queue)


def pending_count() -> int:
    with _lock:
        return sum(1 for t in _queue if t["status"] == TaskStatus.PENDING)


def running_count() -> int:
    with _lock:
        return sum(1 for t in _queue if t["status"] == TaskStatus.RUNNING)


def finished_count() -> int:
    with _lock:
        return sum(1 for t in _queue if t["status"] == TaskStatus.FINISHED)


Command.register(push)
Command.register(push_recurring)
Command.register(execute)
Command.register(delete)
Command.register(stop)