"""A minimal tool plugin: report the size of a directory the assistant is allowed to read.

Plugins run inside the bridge with full trust. Two rules keep a tool safe:
1. Resolve every path with `ctx.resolve`, which enforces the grant.
2. Return an `Operation`; do nothing with side effects before the gateway calls `run`.
"""

from pydantic import Field

from atb.errors import RemoteError
from atb.policy import Kind
from atb.tools.base import Context, Operation, ServerArgs, tool


class DiskUsageArgs(ServerArgs):
    path: str = Field(".", description="Directory to measure")


@tool("disk_usage", DiskUsageArgs, "Total size in bytes of a directory.", read_only=True)
async def disk_usage(ctx: Context, args: DiskUsageArgs) -> Operation:
    path = await ctx.resolve(args.path)

    async def run() -> dict:
        # An argument list, never a shell string: the path cannot inject commands.
        result = await ctx.run(["du", "-sxb", "--", path], timeout=60)
        if not result.stdout:
            raise RemoteError(result.stderr.strip()[-500:] or "du failed")
        return {"path": path, "bytes": int(result.stdout.split()[0])}

    return Operation(run=run, kind=Kind.READ, summary=f"disk usage of {path}")
