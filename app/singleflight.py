"""Share only in-flight work, independently for each event loop and key."""

import asyncio
from collections.abc import Callable, Coroutine, Hashable
from copy import deepcopy
from functools import wraps
from inspect import signature
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession


class SingleFlight:
    def __init__(self):
        self._loops = WeakKeyDictionary()

    @property
    def pending(self) -> int:
        return sum(len(tasks) for tasks in self._loops.values())

    async def run[T](self, key: Hashable, factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        tasks = self._loops.setdefault(asyncio.get_running_loop(), {})
        task = tasks.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            tasks[key] = task

            def done(completed):
                if tasks.get(key) is completed:
                    del tasks[key]
                # Retrieve unobserved failures when every waiter was cancelled.
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(done)
        return await asyncio.shield(task)


flight = SingleFlight()


def coalesce(method):
    """Keep the flight alive through cache commit, using a session it owns.

    A request may leave its dependency/session context after cancellation. The
    shared operation therefore must not borrow that request's database session.
    Including the bind in the key also isolates separate databases and tests.
    """
    parameters = signature(method)

    @wraps(method)
    async def wrapped(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        bound.apply_defaults()
        for name in ("symbol", "pair"):
            if name in bound.arguments:
                bound.arguments[name] = bound.arguments[name].upper()
        if "interval" in bound.arguments:
            bound.arguments["interval"] = bound.arguments["interval"].lower()
        db = bound.arguments.get("db")
        key = (method, tuple((name, value.bind if name == "db" else value)
                             for name, value in bound.arguments.items()))

        async def operation():
            if db is None:
                return await method(*bound.args, **bound.kwargs)
            async with AsyncSession(bind=db.bind, expire_on_commit=False) as session:
                bound.arguments["db"] = session
                return await method(*bound.args, **bound.kwargs)

        return deepcopy(await flight.run(key, operation))

    return wrapped


async def fetch(func, *args, **kwargs):
    """Client method + every argument form the key; callers may mutate results."""
    key = (func, args, tuple(sorted(kwargs.items())))
    result = await flight.run(key, lambda: asyncio.to_thread(func, *args, **kwargs))
    return deepcopy(result)
