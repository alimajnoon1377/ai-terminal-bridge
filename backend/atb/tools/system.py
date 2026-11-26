"""System information, processes, services and logs (Linux targets)."""

import re
import shlex

from pydantic import Field

from ..errors import PolicyDenied, RemoteError, ValidationFailed
from ..policy import Kind, Level
from .base import Context, Operation, ServerArgs, tool

_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9@_.:\-]{0,127}$")

_INFO_SCRIPT = r"""
export LC_ALL=C
echo "##host"; uname -n; uname -s; uname -r; uname -m
echo "##os"; (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || echo unknown
echo "##cpus"; getconf _NPROCESSORS_ONLN
echo "##uptime"; cat /proc/uptime
echo "##load"; cat /proc/loadavg
echo "##mem"; cat /proc/meminfo
echo "##cpu1"; head -n1 /proc/stat
echo "##net1"; cat /proc/net/dev
sleep 1
echo "##cpu2"; head -n1 /proc/stat
echo "##net2"; cat /proc/net/dev
echo "##df"; df -P -k -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null; echo "##dfroot"; df -P -k /
"""


def parse_system_info(text: str) -> dict:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("##"):
            current = line[2:]
            sections[current] = []
        elif current:
            sections[current].append(line)
    host = sections["host"]
    memory = {}
    for line in sections["mem"]:
        key, _, value = line.partition(":")
        if value.split():
            memory[key] = int(value.split()[0]) * 1024

    def cpu_times(name: str) -> tuple[int, int]:
        values = [int(v) for v in sections[name][0].split()[1:9]]
        return sum(values), values[3] + values[4]

    total1, idle1 = cpu_times("cpu1")
    total2, idle2 = cpu_times("cpu2")
    elapsed = total2 - total1
    cpu_percent = round(100 * (1 - (idle2 - idle1) / elapsed), 1) if elapsed > 0 else 0.0

    def net(name: str) -> dict[str, tuple[int, int]]:
        result = {}
        for line in sections[name][2:]:
            interface, _, rest = line.partition(":")
            fields = rest.split()
            if interface.strip() != "lo" and len(fields) >= 9:
                result[interface.strip()] = (int(fields[0]), int(fields[8]))
        return result

    first, second = net("net1"), net("net2")
    network = [
        {"interface": name, "rx_bytes": rx, "tx_bytes": tx,
         "rx_bytes_per_sec": max(0, rx - first.get(name, (rx, tx))[0]),
         "tx_bytes_per_sec": max(0, tx - first.get(name, (rx, tx))[1])}
        for name, (rx, tx) in second.items()
    ]
    disks, seen = [], set()
    for line in sections.get("df", [])[1:] + sections.get("dfroot", [])[1:]:
        fields = line.split()
        if len(fields) >= 6 and fields[1].isdigit() and fields[5] not in seen:
            seen.add(fields[5])
            disks.append({"mount": fields[5], "total_bytes": int(fields[1]) * 1024,
                          "used_bytes": int(fields[2]) * 1024, "available_bytes": int(fields[3]) * 1024})
    total = memory.get("MemTotal", 0)
    available = memory.get("MemAvailable", memory.get("MemFree", 0))
    return {
        "hostname": host[0], "kernel": f"{host[1]} {host[2]}", "architecture": host[3], "os": sections["os"][0],
        "cpu_count": int(sections["cpus"][0]), "cpu_percent": cpu_percent,
        "load_average": [float(v) for v in sections["load"][0].split()[:3]],
        "uptime_seconds": int(float(sections["uptime"][0].split()[0])),
        "memory": {"total_bytes": total, "available_bytes": available, "used_bytes": total - available,
                   "swap_total_bytes": memory.get("SwapTotal", 0), "swap_free_bytes": memory.get("SwapFree", 0)},
        "disks": disks, "network": network,
    }


@tool("get_system_info", ServerArgs, "CPU, memory, disk, network and OS information (Linux).", read_only=True)
async def get_system_info(ctx: Context, args: ServerArgs) -> Operation:
    async def run() -> dict:
        result = await ctx.run(["sh", "-c", _INFO_SCRIPT], timeout=30)
        try:
            return parse_system_info(result.stdout)
        except (KeyError, IndexError, ValueError) as exc:
            raise RemoteError("could not read system information; only Linux targets are supported") from exc

    return Operation(run=run, summary="system information")


class ProcessArgs(ServerArgs):
    filter: str = Field("", max_length=200, description="Case-insensitive text to match in user or command")
    limit: int = Field(50, ge=1, le=500)
    sort: str = Field("cpu", pattern="^(cpu|memory)$")


@tool("get_processes", ProcessArgs, "List running processes sorted by CPU or memory use.", read_only=True)
async def get_processes(ctx: Context, args: ProcessArgs) -> Operation:
    async def run() -> dict:
        order = "-pcpu" if args.sort == "cpu" else "-rss"
        result = await ctx.run(["ps", "-ww", "-eo", "pid=,ppid=,user=,pcpu=,pmem=,rss=,etimes=,stat=,args=", f"--sort={order}"])
        if result.exit_code != 0:
            raise RemoteError(result.stderr.strip()[:500])
        needle, items = args.filter.lower(), []
        for line in result.stdout.splitlines():
            f = line.split(None, 8)
            if len(f) < 9 or (needle and needle not in f"{f[2]} {f[8]}".lower()):
                continue
            items.append({"pid": int(f[0]), "ppid": int(f[1]), "user": f[2], "cpu_percent": float(f[3]),
                          "memory_percent": float(f[4]), "rss_bytes": int(f[5]) * 1024,
                          "elapsed_seconds": int(f[6]), "state": f[7], "command": f[8][:1000]})
        return {"processes": items[: args.limit], "total_matching": len(items)}

    return Operation(run=run, summary="process list")


def valid_name(value: str, what: str) -> str:
    if not _NAME.match(value):
        raise ValidationFailed(f"invalid {what} name")
    return value


def require_service(ctx: Context, name: str) -> str:
    valid_name(name, "service")
    if ctx.grant.level == Level.OWNER:
        return name
    base = name.removesuffix(".service")
    allowed = {item.removesuffix(".service") for item in ctx.grant.services}
    if "*" not in allowed and base not in allowed:
        raise PolicyDenied(f"service {name} is not in the list granted to this assistant")
    return name


class ServiceArgs(ServerArgs):
    action: str = Field(pattern="^(list|status|start|stop|restart|reload|enable|disable)$")
    service: str | None = None


@tool("manage_services", ServiceArgs, "List, inspect or control systemd services.")
async def manage_services(ctx: Context, args: ServiceArgs) -> Operation:
    read = args.action in ("list", "status")
    if args.action != "list" and not args.service:
        raise ValidationFailed("service is required for this action")
    argv = ["systemctl", "--no-pager"]
    if args.action == "list":
        argv += ["list-units", "--type=service", "--all", "--plain", "--no-legend"]
    else:
        name = require_service(ctx, args.service)
        argv += [args.action, "--", name]
        if not read and ctx.grant.use_sudo_for_services:
            argv = ["sudo", "-n", *argv]

    async def run() -> dict:
        result = await ctx.run(argv, timeout=90)
        # `systemctl status` exits 3 for an inactive unit, which is an answer, not a failure.
        if result.exit_code != 0 and not (args.action == "status" and result.stdout):
            raise RemoteError(result.stderr.strip()[:1000] or f"systemctl exited with {result.exit_code}")
        return {"action": args.action, "service": args.service, "exit_code": result.exit_code,
                "output": result.stdout[:60000]}

    return Operation(
        run=run, kind=Kind.READ if read else Kind.SERVICE,
        summary=f"systemctl {args.action} {args.service or ''}".strip(), preview=shlex.join(argv),
        destructive="stops or disables a service" if args.action in ("stop", "disable") else "",
    )


class LogArgs(ServerArgs):
    source: str = Field(pattern="^(journal|file|docker)$")
    name: str = Field(description="systemd unit, file path, or container name")
    lines: int = Field(200, ge=1, le=2000)


@tool("get_logs", LogArgs, "Read the last lines of a service journal, a log file, or a container log.", read_only=True)
async def get_logs(ctx: Context, args: LogArgs) -> Operation:
    if args.source == "file":
        path = await ctx.resolve(args.name)
        argv = ["tail", "-n", str(args.lines), "--", path]
    elif args.source == "journal":
        argv = ["journalctl", "--no-pager", "-n", str(args.lines), "-u", require_service(ctx, args.name)]
    else:
        if ctx.grant.level != Level.OWNER and not ctx.grant.allow_docker:
            raise PolicyDenied("Docker access is not enabled for this grant")
        argv = ["docker", "logs", "--tail", str(args.lines), "--", valid_name(args.name, "container")]

    async def run() -> dict:
        command = f"{shlex.join(argv)} 2>&1 | tail -c 200000"
        result = await ctx.run(["bash", "-c", command + '; exit "${PIPESTATUS[0]}"'], timeout=60)
        if result.exit_code != 0:
            raise RemoteError(result.stdout.strip()[-1000:] or f"command exited with {result.exit_code}")
        return {"source": args.source, "name": args.name, "log": result.stdout}

    return Operation(run=run, summary=f"logs {args.source}:{args.name}")
