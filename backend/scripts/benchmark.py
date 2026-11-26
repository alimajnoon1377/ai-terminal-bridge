#!/usr/bin/env python3
"""Latency and concurrency benchmark against a running bridge.

    ATB_URL=http://127.0.0.1:8750 ATB_AGENT_TOKEN=atbag_... \
        python scripts/benchmark.py --server NAME --dir /srv/project --requests 100 --concurrency 10

The assistant needs a grant with command execution on that directory (operator or autonomous).
The default rate limit (120/minute) applies: raise it for this assistant first.
"""

import argparse
import asyncio
import os
import statistics
import time

import httpx


async def measure(client: httpx.AsyncClient, name: str, arguments: dict, total: int, concurrency: int) -> None:
    gate = asyncio.Semaphore(concurrency)
    times: list[float] = []
    failures: dict[str, int] = {}

    async def one() -> None:
        async with gate:
            start = time.perf_counter()
            response = await client.post(f"/api/v1/tools/{name}", json=arguments)
            body = response.json()
            if response.is_error or (name == "execute_command" and body.get("status") != "succeeded"):
                reason = str(body.get("message") or body.get("status"))[:100]
                failures[reason] = failures.get(reason, 0) + 1
            else:
                times.append(time.perf_counter() - start)

    began = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    elapsed = time.perf_counter() - began
    if times:
        ordered = sorted(times)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        print(f"{name:16} n={len(times):4} c={concurrency:3} median={statistics.median(times) * 1000:7.1f} ms "
              f"p95={p95 * 1000:7.1f} ms  {len(times) / elapsed:6.1f} req/s")
    for reason, count in failures.items():
        print(f"{name:16} failed x{count}: {reason}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4,
                        help="simultaneous requests; commands are also capped by the grant's max_concurrent_jobs")
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=os.environ.get("ATB_URL", "http://127.0.0.1:8750"), timeout=120,
                                 headers={"Authorization": f"Bearer {os.environ['ATB_AGENT_TOKEN']}"}) as client:
        path = f"{args.dir.rstrip('/')}/atb-benchmark.txt"
        response = await client.post("/api/v1/tools/write_file", json={"server": args.server, "path": path, "content": "x" * 10_000})
        response.raise_for_status()
        await measure(client, "read_file", {"server": args.server, "path": path}, args.requests, args.concurrency)
        await measure(client, "list_directory", {"server": args.server, "path": args.dir}, args.requests, args.concurrency)
        await measure(client, "execute_command", {"server": args.server, "command": "true", "cwd": args.dir, "wait_seconds": 30},
                      args.requests, args.concurrency)


if __name__ == "__main__":
    asyncio.run(main())
