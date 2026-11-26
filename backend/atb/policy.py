"""Access levels, grant configuration and the allow / approve / deny decision."""

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from posixpath import normpath
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .errors import PolicyDenied


class Level(StrEnum):
    READ_ONLY = "read_only"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    APPROVAL_REQUIRED = "approval_required"
    AUTONOMOUS = "autonomous"
    OWNER = "owner"
    """Internal level for a signed-in human acting on their own server."""


class Kind(StrEnum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    SERVICE = "service"
    DOCKER = "docker"


class Decision(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"


DEFAULT_DENY_GLOBS = [
    "*/.ssh",
    "*/.ssh/*",
    "*/id_rsa*",
    "*/id_ed25519*",
    "/etc/shadow*",
    "/etc/gshadow*",
    "/etc/sudoers*",
    "*/.aws/credentials",
    "*/.git-credentials",
    "*/.netrc",
]
# The bridge's own state on the remote host is never reachable by an assistant.
ALWAYS_DENY_GLOBS = ["*/.atb", "*/.atb/*"]


class PathRule(BaseModel):
    path: str
    mode: Literal["ro", "rw"] = "rw"

    @field_validator("path")
    @classmethod
    def absolute(cls, value: str) -> str:
        if not value.startswith("/") or "\0" in value or "\n" in value:
            raise ValueError("path rules must be absolute POSIX paths")
        return "/" + normpath(value).lstrip("/")


class GrantConfig(BaseModel):
    level: Level = Level.READ_ONLY
    paths: list[PathRule] = Field(default_factory=list)
    deny_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY_GLOBS))
    services: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    denied_patterns: list[str] = Field(default_factory=list)
    force_sandbox: bool = False
    sandbox_network: bool = False
    allow_docker: bool = False
    use_sudo_for_services: bool = False
    auto_approve_destructive: bool = False
    max_runtime_seconds: int = Field(600, ge=1, le=86400)
    max_output_kb: int = Field(2048, ge=1, le=102400)
    max_memory_mb: int = Field(0, ge=0, le=1_048_576)
    max_concurrent_jobs: int = Field(4, ge=1, le=64)

    @field_validator("level")
    @classmethod
    def assignable(cls, value: Level) -> Level:
        if value == Level.OWNER:
            raise ValueError("the owner level cannot be granted to an assistant")
        return value

    @field_validator("denied_patterns")
    @classmethod
    def compiles(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
        return value


def owner_grant() -> GrantConfig:
    config = GrantConfig.model_construct(**GrantConfig().model_dump())
    config.level = Level.OWNER
    config.paths = [PathRule(path="/", mode="rw")]
    config.deny_globs = []
    config.max_concurrent_jobs = 64
    return config


# Files that run automatically outside any sandbox; assistants may read but never write them.
PROTECTED_WRITE_GLOBS = [
    "*/.bashrc", "*/.bash_profile", "*/.bash_login", "*/.bash_logout", "*/.profile",
    "*/.zshrc", "*/.zshenv", "*/.zprofile", "*/.config/systemd/*", "*/.config/autostart/*",
]


def is_within(path: str, root: str) -> bool:
    return root == "/" or path == root or path.startswith(root + "/")


def rule_for(path: str, rules: list[PathRule]) -> PathRule | None:
    """Most specific matching rule wins, so a read-only subfolder can sit inside a writable one."""
    matches = [rule for rule in rules if is_within(path, rule.path)]
    return max(matches, key=lambda rule: len(rule.path), default=None)


def is_denied_path(path: str, config: GrantConfig) -> bool:
    globs = list(config.deny_globs)
    if config.level != Level.OWNER:
        globs += ALWAYS_DENY_GLOBS
    return any(fnmatchcase(path, pattern) for pattern in globs)


def check_path(path: str, config: GrantConfig, *, write: bool) -> None:
    """`path` must already be canonical (symlinks resolved on the target)."""
    if is_denied_path(path, config):
        raise PolicyDenied(f"access to {path} is blocked by a deny rule")
    if write and config.level != Level.OWNER and any(fnmatchcase(path, p) for p in PROTECTED_WRITE_GLOBS):
        raise PolicyDenied(f"{path} runs automatically at login and cannot be written by an assistant")
    rule = rule_for(path, config.paths)
    if rule is None:
        raise PolicyDenied(f"{path} is outside the paths granted to this assistant")
    if write and rule.mode != "rw":
        raise PolicyDenied(f"{path} is granted read-only")


# Commands that are refused outright for assistants.
_CATASTROPHIC = [
    r"\brm\s+(?:-[a-zA-Z]+\s+|--[a-z-]+\s+)*(?:/|/\*)(?:\s|$|;)",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\b[^|;&]*\bof=/dev/(?:sd|vd|nvme|hd|mmcblk)",
    r">\s*/dev/(?:sd|vd|nvme|hd|mmcblk)",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
]

_DESTRUCTIVE = [
    ("removes files", r"\b(?:rm|rmdir|unlink|shred|truncate)\b"),
    ("deletes via find", r"\bfind\b[^|;&]*\s-delete\b"),
    ("reboots or powers off", r"\b(?:shutdown|reboot|halt|poweroff)\b|\binit\s+[06]\b"),
    ("stops or disables a service", r"\b(?:systemctl|service)\b[^|;&]*\b(?:stop|disable|mask|kill)\b"),
    ("kills processes", r"\b(?:kill|pkill|killall)\b"),
    ("elevates privileges", r"\b(?:sudo|doas|su)\b"),
    ("changes permissions recursively", r"\bch(?:mod|own|grp)\b[^|;&]*\s-[a-zA-Z]*R"),
    ("rewrites git history or discards work", r"\bgit\b[^|;&]*(?:\bpush\b[^|;&]*(?:--force|-f\b)|\breset\s+--hard\b|\bclean\s+-[a-zA-Z]*f)"),
    ("pipes a download into an interpreter", r"\b(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|\b(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:python|perl|ruby|node)"),
    ("removes containers or images", r"\bdocker\b[^|;&]*\b(?:rm|rmi|prune|down|kill)\b"),
    ("drops database objects", r"\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b"),
    ("manages packages", r"\b(?:apt|apt-get|dnf|yum|apk|pacman)\b[^|;&]*\b(?:remove|purge|autoremove|del)\b"),
    ("edits users or firewall", r"\b(?:userdel|usermod|passwd|iptables|nft|ufw|crontab)\b"),
]


def destructive_reasons(command: str) -> list[str]:
    """Heuristic risk labels. A determined caller can evade these; see docs/SECURITY.md."""
    return [label for label, pattern in _DESTRUCTIVE if re.search(pattern, command, re.I)]


def check_command(command: str, config: GrantConfig) -> str:
    """Apply deny rules and the allow list; return the command line to execute."""
    if "\0" in command:
        raise PolicyDenied("command contains a NUL byte")
    if config.level == Level.OWNER:
        return command
    for pattern in _CATASTROPHIC:
        if re.search(pattern, command):
            raise PolicyDenied("command matches a built-in rule for irreversible system damage")
    for pattern in config.denied_patterns:
        if re.search(pattern, command):
            raise PolicyDenied(f"command matches denied pattern {pattern!r}")
    if config.allowed_commands:
        # With an allow list the command is a single program invocation. It is re-quoted
        # so that the shell cannot interpret operators, substitutions or redirects.
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise PolicyDenied(f"cannot parse command: {exc}") from exc
        if not argv or argv[0] not in config.allowed_commands:
            raise PolicyDenied(f"only these programs are allowed: {', '.join(config.allowed_commands)}")
        return shlex.join(argv)
    return command


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str = ""


def decide(config: GrantConfig, kind: Kind, *, destructive: str = "", sandboxed: bool = False) -> Verdict:
    """Return ALLOW or APPROVE, or raise PolicyDenied."""
    level = config.level
    if level == Level.OWNER or kind == Kind.READ:
        return Verdict(Decision.ALLOW)
    if level == Level.READ_ONLY:
        raise PolicyDenied("this assistant has read-only access to the server")
    if kind == Kind.DOCKER and not config.allow_docker:
        raise PolicyDenied("Docker control is not enabled for this grant")
    if level == Level.DEVELOPER and kind in (Kind.SERVICE, Kind.DOCKER):
        raise PolicyDenied("the developer level cannot manage services or containers")
    if level == Level.APPROVAL_REQUIRED:
        return Verdict(Decision.APPROVE, destructive or f"{kind.value} operations require approval")
    if destructive and not (level == Level.AUTONOMOUS and config.auto_approve_destructive):
        return Verdict(Decision.APPROVE, destructive)
    if level == Level.DEVELOPER and kind == Kind.EXEC and not sandboxed:
        return Verdict(
            Decision.APPROVE,
            "no sandbox is available on this server, so the command would not be confined to the granted paths",
        )
    return Verdict(Decision.ALLOW)
