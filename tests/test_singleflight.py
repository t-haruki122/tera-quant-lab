import asyncio

from app.singleflight import SingleFlight, fetch


def test_singleflight_success_failure_keys_and_cleanup():
    async def scenario():
        flight = SingleFlight()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def worker():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return 42

        waiters = [asyncio.create_task(flight.run("same", worker)) for _ in range(10)]
        await entered.wait()
        assert await flight.run("different", lambda: asyncio.sleep(0, result=7)) == 7
        assert calls == 1
        release.set()
        assert await asyncio.gather(*waiters) == [42] * 10
        assert flight.pending == 0

        failure = ValueError("shared")
        release.clear()

        async def failing():
            await release.wait()
            raise failure

        waiters = [asyncio.create_task(flight.run("fail", failing)) for _ in range(10)]
        await asyncio.sleep(0)
        release.set()
        assert all(error is failure for error in await asyncio.gather(*waiters, return_exceptions=True))
        assert flight.pending == 0

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_shared_work_even_if_all_waiters_leave():
    async def scenario():
        flight = SingleFlight()
        release = asyncio.Event()
        entered = asyncio.Event()

        async def worker():
            entered.set()
            return await release.wait()

        first = asyncio.create_task(flight.run("key", worker))
        second = asyncio.create_task(flight.run("key", worker))
        await entered.wait()
        first.cancel()
        assert isinstance((await asyncio.gather(first, return_exceptions=True))[0], asyncio.CancelledError)
        assert flight.pending == 1
        release.set()
        assert await second is True
        assert flight.pending == 0
        release.clear()
        entered.clear()
        last = asyncio.create_task(flight.run("key", worker))
        await entered.wait()
        last.cancel()
        await asyncio.gather(last, return_exceptions=True)
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert flight.pending == 0

    asyncio.run(scenario())


def test_fetch_includes_all_arguments_and_returns_independent_values():
    async def scenario():
        def client(symbol, limit=6):
            return {"symbol": symbol, "history": [limit]}

        one, two, other = await asyncio.gather(fetch(client, "A", limit=6), fetch(client, "A", limit=6),
                                               fetch(client, "A", limit=8))
        one["history"].append(9)
        assert two["history"] == [6]
        assert other["history"] == [8]

    asyncio.run(scenario())
