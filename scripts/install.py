#!/usr/bin/env python3
"""管理一个本地插件或独立 skill，并发布到 Claude、Codex、Grok。

复制本文件到项目的 ``scripts/install.py`` 后，只修改顶部的项目配置区。
其余同步引擎逻辑保持原样；不要创建额外的 JSON 或配置模块。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLIENTS = ("claude", "codex", "grok")
COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_HOME_SUFFIXES = {"claude": ".claude", "codex": ".codex", "grok": ".grok"}
SAFE_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )


class SyncError(RuntimeError):
    """报告预检、客户端命令或受管目录失败。"""


@dataclass(frozen=True)
class Marketplace:
    name: str
    root: Path


@dataclass(frozen=True)
class PluginConfig:
    root: Path
    plugin_name: str
    package: Path
    marketplaces: dict[str, Marketplace]
    executables: dict[str, str]


@dataclass(frozen=True)
class SkillConfig:
    name: str
    source: Path
    executables: dict[str, str]


@dataclass(frozen=True)
class ReleaseConfig:
    """项目配置；同步策略和客户端适配器不属于项目配置。"""

    plugins: tuple[PluginConfig, ...] = ()
    skill: SkillConfig | None = None


# ===== 项目配置区：复制到项目后只修改本区 =====
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parent

PLUGIN_NAMES = ("dbtalk",)
PLUGIN_PACKAGES = {"dbtalk": REPOSITORY_ROOT / "plugins" / "dbtalk"}
PLUGIN_MARKETPLACE_ROOT = REPOSITORY_ROOT
PLUGIN_MARKETPLACE_NAME = "dbtalk-local"

STANDALONE_SKILL_NAME: str | None = None
STANDALONE_SKILL_SOURCE: Path | None = None

EXECUTABLES = {client: client for client in CLIENTS}
# ===== 项目配置区结束 =====


def configured_release() -> ReleaseConfig:
    marketplaces = {
        "claude": Marketplace(PLUGIN_MARKETPLACE_NAME, PLUGIN_MARKETPLACE_ROOT),
        "codex": Marketplace(PLUGIN_MARKETPLACE_NAME, PLUGIN_MARKETPLACE_ROOT),
    }
    plugins = tuple(
        PluginConfig(
            root=REPOSITORY_ROOT,
            plugin_name=name,
            package=PLUGIN_PACKAGES[name],
            marketplaces=marketplaces,
            executables=EXECUTABLES,
        )
        for name in PLUGIN_NAMES
    )

    skill = None
    if STANDALONE_SKILL_NAME is not None:
        if STANDALONE_SKILL_SOURCE is None:
            raise SyncError(
                "STANDALONE_SKILL_SOURCE must be configured when "
                "STANDALONE_SKILL_NAME is set"
            )
        skill = SkillConfig(
            name=STANDALONE_SKILL_NAME,
            source=STANDALONE_SKILL_SOURCE,
            executables=EXECUTABLES,
        )

    return ReleaseConfig(
        plugins=plugins,
        skill=skill,
    )


@dataclass(frozen=True)
class ClientTarget:
    name: str
    executable: str
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Command:
    arguments: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class ClientPlan:
    client: str
    commands: tuple[Command, ...]
    verification: tuple[str, ...]
    selector: str


@dataclass
class Outcome:
    client: str
    status: str
    detail: str
    actions: list[str] = field(default_factory=list)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.asset is None or args.command is None:
        parser.print_help()
        return 0
    config = configured_release()
    if args.strict and any((args.claude, args.codex, args.grok)):
        parser.error("--strict 不能与显式客户端选择同时使用")
    if args.command in {"check", "list"} and args.dry_run:
        parser.error("--dry-run 只能用于 apply 或 remove")

    outcomes: list[Outcome] = []
    try:
        target_config: PluginConfig | SkillConfig | None
        if args.asset == "plugin":
            if not config.plugins:
                raise SyncError("project release configuration does not define plugin")
            target_config = config.plugins[0]
        else:
            target_config = config.skill
        if target_config is None:
            raise SyncError(f"project release configuration does not define {args.asset}")
        if isinstance(target_config, SkillConfig) and not SAFE_SKILL_NAME.fullmatch(
            target_config.name
        ):
            raise SyncError(
                "standalone skill name can only contain letters, digits, underscores, and hyphens"
            )
        requested, explicit = requested_clients(args)
        targets, selection = select_targets(target_config, requested, explicit, args.strict)
        outcomes.extend(selection)
        if isinstance(target_config, PluginConfig):
            exit_code = manage_plugin(
                config.plugins, args.command, targets, args.dry_run, outcomes
            )
        else:
            exit_code = manage_skill(
                target_config, args.command, targets, args.dry_run, outcomes
            )
    except SyncError as error:
        logging.exception(
            "sync failed asset=%s command=%s", args.asset, args.command
        )
        outcomes.append(Outcome("global", "failed", str(error)))
        return 1
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理本地 plugin 或独立 skill 到已安装的 Claude、Codex、Grok 客户端。"
    )
    parser.add_argument("asset", nargs="?", choices=("plugin", "skill"), help="要管理的资产类型")
    parser.add_argument("command", nargs="?", choices=("check", "list", "apply", "remove"))
    parser.add_argument("--claude", action="store_true", help="仅选择 Claude Code")
    parser.add_argument("--codex", action="store_true", help="仅选择 Codex")
    parser.add_argument("--grok", action="store_true", help="仅选择 Grok Build")
    parser.add_argument("--strict", action="store_true", help="写入前要求三个客户端均可用")
    parser.add_argument(
        "--dry-run", action="store_true", help="规划 apply 或 remove，不修改本机状态"
    )
    return parser


def requested_clients(args: argparse.Namespace) -> tuple[tuple[str, ...], bool]:
    requested = tuple(client for client in CLIENTS if getattr(args, client))
    return requested or CLIENTS, bool(requested)


def select_targets(
    config: PluginConfig | SkillConfig,
    requested: Sequence[str],
    explicit: bool,
    strict: bool,
    find_command: Callable[[str], str | None] = shutil.which,
) -> tuple[list[ClientTarget], list[Outcome]]:
    missing: list[str] = []
    ready: list[ClientTarget] = []
    outcomes: list[Outcome] = []
    for client in requested:
        executable = find_command(config.executables[client])
        if executable is None:
            detail = f"{config.executables[client]} command is not installed"
            if explicit or strict:
                missing.append(f"{client}: {detail}")
            else:
                asset = "plugin" if isinstance(config, PluginConfig) else "skill"
                logging.warning(
                    "client=%s asset=%s action=skip reason=%s",
                    client,
                    asset,
                    detail,
                )
                outcomes.append(Outcome(client, "skipped", detail))
            continue
        environment = plugin_environment() if isinstance(config, PluginConfig) else {}
        ready.append(ClientTarget(client, executable, environment))
    if missing:
        raise SyncError(
            "required client commands are unavailable: " + "; ".join(missing)
        )
    if not ready:
        raise SyncError("no selected client command is installed")
    return ready, outcomes


def plugin_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for variable in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME"):
        environment.pop(variable, None)
    return environment


class CommandRunner:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def inspect(self, target: ClientTarget, arguments: Sequence[str]) -> Any:
        completed = self._run(target, arguments)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SyncError(
                f"{target.name} returned invalid JSON while reading plugin state"
            ) from error

    def change(self, target: ClientTarget, arguments: Sequence[str]) -> None:
        self._run(target, arguments)

    def _run(
        self, target: ClientTarget, arguments: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [target.executable, *arguments],
                cwd=self.cwd,
                env=target.environment or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except OSError as error:
            raise SyncError(f"could not execute {target.name} CLI") from error
        except subprocess.TimeoutExpired as error:
            raise SyncError(
                f"{target.name} CLI timed out while running plugin command"
            ) from error
        if completed.returncode != 0:
            raise SyncError(
                f"{target.name} CLI failed while running {' '.join(arguments)}"
            )
        return completed


def manage_plugin(
    configs: Sequence[PluginConfig],
    command: str,
    targets: Sequence[ClientTarget],
    dry_run: bool,
    outcomes: list[Outcome],
) -> int:
    runners = {config.plugin_name: CommandRunner(config.root) for config in configs}
    if command == "list":
        for target in targets:
            for config in configs:
                outcomes.append(
                    list_plugin(config, target, runners[config.plugin_name])
                )
        return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0

    plans_by_plugin: list[tuple[PluginConfig, Sequence[ClientPlan]]] = []
    for config in configs:
        runner = runners[config.plugin_name]
        plans: Sequence[ClientPlan]
        if command == "remove":
            plans = [plan_plugin_removal(config, target, runner) for target in targets]
        else:
            validate_plugin_sources(config, (target.name for target in targets))
            plans = [plan_plugin(config, target, runner) for target in targets]
        plans_by_plugin.append((config, plans))

    failed = False
    for target_index, target in enumerate(targets):
        for config, plans in plans_by_plugin:
            plan = plans[target_index]
            if command == "check":
                logging.info(
                    "client=%s plugin=%s action=%s",
                    plan.client,
                    plan.selector,
                    describe_actions(action_labels(plan)),
                )
                outcomes.append(outcome_for_plan(plan, "ready"))
                continue
            if dry_run:
                logging.info(
                    "client=%s plugin=%s action=%s",
                    plan.client,
                    plan.selector,
                    describe_actions(action_labels(plan)),
                )
                outcomes.append(outcome_for_plan(plan, "planned"))
                continue

            runner = runners[config.plugin_name]
            if command == "remove":
                failed = (
                    execute_plugin_removal(config, target, plan, runner, outcomes)
                    or failed
                )
            else:
                failed = (
                    execute_plugin_apply(config, target, plan, runner, outcomes)
                    or failed
                )
    return 1 if failed or any(outcome.status == "failed" for outcome in outcomes) else 0


def validate_plugin_sources(config: PluginConfig, clients: Iterable[str]) -> None:
    selected = set(clients)
    if not config.package.is_dir():
        raise SyncError(f"plugin package does not exist: {config.package}")
    if not any((config.package / "skills").glob("*/SKILL.md")):
        raise SyncError(
            f"plugin package must contain skills/*/SKILL.md: "
            f"{config.package / 'skills'}"
        )
    if selected & {"claude", "grok"}:
        validate_manifest(
            config.package / ".claude-plugin" / "plugin.json",
            config.plugin_name,
            "Claude",
        )
    if "codex" in selected:
        manifest = config.package / ".codex-plugin" / "plugin.json"
        data = validate_manifest(manifest, config.plugin_name, "Codex")
        if data.get("skills") != "./skills/":
            raise SyncError(
                f"Codex manifest must declare skills as ./skills/: {manifest}"
            )
    if "claude" in selected:
        validate_marketplace(config, "claude")
    if "codex" in selected:
        validate_marketplace(config, "codex")


def validate_manifest(path: Path, plugin_name: str, client: str) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict) or data.get("name") != plugin_name:
        raise SyncError(f"{client} manifest name must equal PLUGIN_NAME: {path}")
    return data


def validate_marketplace(config: PluginConfig, client: str) -> None:
    marketplace = config.marketplaces[client]
    manifest = marketplace.root / (
        ".claude-plugin/marketplace.json"
        if client == "claude"
        else ".agents/plugins/marketplace.json"
    )
    data = read_json(manifest)
    if not isinstance(data, dict) or data.get("name") != marketplace.name:
        raise SyncError(
            f"{client} marketplace name must equal PLUGIN_MARKETPLACE_NAME: {manifest}"
        )
    if client == "claude":
        owner = data.get("owner")
        if (
            not isinstance(owner, dict)
            or not isinstance(owner.get("name"), str)
            or not owner["name"].strip()
        ):
            raise SyncError(
                f"Claude marketplace owner.name must be a non-empty string: {manifest}"
            )
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise SyncError(
            f"{client} marketplace plugins must be a JSON array: {manifest}"
        )
    entries = [
        entry
        for entry in plugins
        if isinstance(entry, dict) and entry.get("name") == config.plugin_name
    ]
    if len(entries) != 1:
        raise SyncError(
            f"{client} marketplace must contain exactly one plugin "
            f"named {config.plugin_name}"
        )
    source = marketplace_source_path(entries[0], client)
    if not same_path(resolve_path(marketplace.root, source), config.package):
        raise SyncError(f"{client} marketplace source must resolve to {config.package}")


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except OSError as error:
        raise SyncError(f"could not read JSON file: {path}") from error
    except json.JSONDecodeError as error:
        raise SyncError(f"invalid JSON file: {path}") from error


def marketplace_source_path(entry: dict[str, Any], client: str) -> str:
    source = entry.get("source")
    if client == "claude" and isinstance(source, str):
        return source
    if (
        client == "codex"
        and isinstance(source, dict)
        and source.get("source") == "local"
    ):
        path = source.get("path")
        if isinstance(path, str):
            return path
    raise SyncError(f"{client} marketplace must use a local source path")


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def plan_plugin(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> ClientPlan:
    if target.name == "claude":
        return plan_claude(config, target, runner)
    if target.name == "codex":
        return plan_codex(config, target, runner)
    return plan_grok(config, target, runner)


def plan_claude(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> ClientPlan:
    marketplace = config.marketplaces["claude"]
    commands: list[Command] = []
    if not has_field_value(
        records(
            runner.inspect(target, ("plugin", "marketplace", "list", "--json")),
            "marketplaces",
        ),
        "name",
        marketplace.name,
    ):
        commands.append(
            Command(
                (
                    "plugin",
                    "marketplace",
                    "add",
                    str(marketplace.root),
                    "--scope",
                    "user",
                ),
                f"register marketplace {marketplace.name}",
            )
        )
    selector = f"{config.plugin_name}@{marketplace.name}"
    installed = records(runner.inspect(target, ("plugin", "list", "--json")), "plugins")
    action = "update" if has_field_value(installed, "id", selector) else "install"
    commands.append(
        Command(
            ("plugin", action, selector, "--scope", "user"),
            f"{action} plugin {selector}",
        )
    )
    return ClientPlan("claude", tuple(commands), ("plugin", "list", "--json"), selector)


def plan_codex(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> ClientPlan:
    marketplace = config.marketplaces["codex"]
    registered = exact_record(
        records(
            runner.inspect(target, ("plugin", "marketplace", "list", "--json")),
            "marketplaces",
        ),
        "name",
        marketplace.name,
    )
    commands: list[Command] = []
    if registered is None:
        commands.append(
            Command(
                ("plugin", "marketplace", "add", str(marketplace.root), "--json"),
                f"register marketplace {marketplace.name}",
            )
        )
    elif isinstance(registered.get("root"), str) and not same_path(
        registered["root"], marketplace.root
    ):
        raise SyncError(
            f"Codex marketplace {marketplace.name} is registered "
            "from a different source"
        )
    selector = f"{config.plugin_name}@{marketplace.name}"
    commands.append(
        Command(
            ("plugin", "add", selector, "--json"),
            f"install or refresh plugin {selector}",
        )
    )
    return ClientPlan("codex", tuple(commands), ("plugin", "list", "--json"), selector)


def plan_grok(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> ClientPlan:
    installed = records(runner.inspect(target, ("plugin", "list", "--json")), "plugins")
    installed_plugin = exact_record(installed, "name", config.plugin_name)
    commands: tuple[Command, ...]
    if installed_plugin is None:
        commands = (
            Command(
                ("plugin", "install", str(config.package), "--trust"),
                f"install trusted plugin {config.plugin_name}",
            ),
        )
    else:
        source = installed_plugin.get("source")
        if not isinstance(source, str):
            raise SyncError(
                f"Grok plugin {config.plugin_name} does not report its source"
            )
        if same_path(resolve_path(config.root, source), config.package):
            commands = (
                Command(
                    ("plugin", "update", config.plugin_name),
                    f"update plugin {config.plugin_name}",
                ),
            )
        else:
            commands = (
                Command(
                    ("plugin", "uninstall", config.plugin_name, "--confirm"),
                    f"remove plugin {config.plugin_name} from previous source",
                ),
                Command(
                    ("plugin", "install", str(config.package), "--trust"),
                    f"install trusted plugin {config.plugin_name}",
                ),
            )
    return ClientPlan(
        "grok", commands, ("plugin", "list", "--json"), config.plugin_name
    )


def list_plugin(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> Outcome:
    if target.name == "claude":
        selector = f"{config.plugin_name}@{config.marketplaces['claude'].name}"
        found = has_field_value(
            records(runner.inspect(target, ("plugin", "list", "--json")), "plugins"),
            "id",
            selector,
        )
    elif target.name == "codex":
        selector = f"{config.plugin_name}@{config.marketplaces['codex'].name}"
        found = codex_has_plugin(
            records(runner.inspect(target, ("plugin", "list", "--json")), "installed"),
            config,
            selector,
        )
    else:
        selector = config.plugin_name
        found = has_field_value(
            records(runner.inspect(target, ("plugin", "list", "--json")), "plugins"),
            "name",
            selector,
        )
    state = "installed" if found else "absent"
    logging.info(
        "client=%s plugin=%s action=list state=%s",
        target.name,
        selector,
        state,
    )
    return Outcome(target.name, state, selector)


def plan_plugin_removal(
    config: PluginConfig, target: ClientTarget, runner: CommandRunner
) -> ClientPlan:
    if target.name == "claude":
        selector = f"{config.plugin_name}@{config.marketplaces['claude'].name}"
        installed = records(
            runner.inspect(target, ("plugin", "list", "--json")), "plugins"
        )
        commands = (
            (
                Command(
                    ("plugin", "uninstall", selector, "--scope", "user"),
                    f"uninstall plugin {selector}",
                ),
            )
            if has_field_value(installed, "id", selector)
            else ()
        )
    elif target.name == "codex":
        selector = f"{config.plugin_name}@{config.marketplaces['codex'].name}"
        installed = records(
            runner.inspect(target, ("plugin", "list", "--json")), "installed"
        )
        commands = (
            (
                Command(
                    ("plugin", "remove", selector, "--json"),
                    f"remove plugin {selector}",
                ),
            )
            if codex_has_plugin(installed, config, selector)
            else ()
        )
    else:
        selector = config.plugin_name
        installed = records(
            runner.inspect(target, ("plugin", "list", "--json")), "plugins"
        )
        commands = (
            (
                Command(
                    ("plugin", "uninstall", selector, "--confirm"),
                    f"uninstall plugin {selector}",
                ),
            )
            if has_field_value(installed, "name", selector)
            else ()
        )
    return ClientPlan(target.name, commands, ("plugin", "list", "--json"), selector)


def codex_has_plugin(
    installed: Iterable[dict[str, Any]], config: PluginConfig, selector: str
) -> bool:
    return any(
        item.get("name") == config.plugin_name
        and item.get("marketplaceName") == config.marketplaces["codex"].name
        for item in installed
    )


def execute_plugin_apply(
    config: PluginConfig,
    target: ClientTarget,
    plan: ClientPlan,
    runner: CommandRunner,
    outcomes: list[Outcome],
) -> bool:
    try:
        for command in plan.commands:
            logging.info(
                "client=%s plugin=%s action=%s",
                plan.client,
                plan.selector,
                command.label,
            )
            runner.change(target, command.arguments)
        verify_plugin(config, target, plan, runner, expected=False)
    except SyncError as error:
        logging.error(
            "client=%s plugin=%s action=failed reason=%s",
            plan.client,
            plan.selector,
            error,
        )
        outcomes.append(Outcome(plan.client, "failed", str(error), action_labels(plan)))
        return True

    outcomes.append(
        Outcome(
            plan.client,
            "applied",
            f"plugin {plan.selector} is installed or updated",
            action_labels(plan),
        )
    )
    return False


def execute_plugin_removal(
    config: PluginConfig,
    target: ClientTarget,
    plan: ClientPlan,
    runner: CommandRunner,
    outcomes: list[Outcome],
) -> bool:
    if not plan.commands:
        logging.info(
            "client=%s plugin=%s action=remove skipped=not-installed",
            plan.client,
            plan.selector,
        )
        outcomes.append(
            Outcome(plan.client, "absent", f"plugin {plan.selector} is not installed")
        )
        return False
    try:
        for command in plan.commands:
            logging.info(
                "client=%s plugin=%s action=%s",
                plan.client,
                plan.selector,
                command.label,
            )
            runner.change(target, command.arguments)
        verify_plugin(config, target, plan, runner, expected=True)
    except SyncError as error:
        logging.error(
            "client=%s plugin=%s action=failed reason=%s",
            plan.client,
            plan.selector,
            error,
        )
        outcomes.append(Outcome(plan.client, "failed", str(error), action_labels(plan)))
        return True

    outcomes.append(
        Outcome(
            plan.client,
            "removed",
            f"plugin {plan.selector} was removed",
            action_labels(plan),
        )
    )
    return False


def verify_plugin(
    config: PluginConfig,
    target: ClientTarget,
    plan: ClientPlan,
    runner: CommandRunner,
    expected: bool,
) -> None:
    payload = runner.inspect(target, plan.verification)
    if target.name == "claude":
        found = has_field_value(records(payload, "plugins"), "id", plan.selector)
    elif target.name == "codex":
        found = codex_has_plugin(records(payload, "installed"), config, plan.selector)
    else:
        found = has_field_value(records(payload, "plugins"), "name", plan.selector)
    if found == expected:
        state = "still reports" if expected else "did not report"
        raise SyncError(f"{target.name} {state} {plan.selector}")


def manage_skill(
    config: SkillConfig,
    command: str,
    targets: Sequence[ClientTarget],
    dry_run: bool,
    outcomes: list[Outcome],
) -> int:
    if command in {"check", "apply"}:
        validate_skill_source(config)
    if command == "check":
        for target in targets:
            logging.info(
                "client=%s skill=%s action=publish skill leaf directory",
                target.name,
                config.name,
            )
            outcomes.append(Outcome(target.name, "ready", "skill source validated"))
    elif command == "list":
        outcomes.extend(list_skill(config, target) for target in targets)
    elif command == "apply":
        outcomes.extend(apply_skill(config, target, dry_run) for target in targets)
    else:
        outcomes.extend(remove_skill(config, target, dry_run) for target in targets)
    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


def validate_skill_source(config: SkillConfig) -> None:
    skill_file = config.source / "SKILL.md"
    if not config.source.is_dir() or not skill_file.is_file():
        raise SyncError(
            f"skill source must be a directory containing SKILL.md: {config.source}"
        )
    if skill_frontmatter_name(skill_file) != config.name:
        raise SyncError(
            f"SKILL.md frontmatter name must equal STANDALONE_SKILL_NAME: {skill_file}"
        )


def skill_frontmatter_name(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SyncError(f"could not read skill file: {path}") from error
    if not text.startswith("---"):
        return None
    match = re.match(r"---\s*\n(.*?)\n---", text, flags=re.DOTALL)
    if match is None:
        return None
    name_match = re.search(
        r"^name:\s*['\"]?([^'\"\s#]+)", match.group(1), flags=re.MULTILINE
    )
    return name_match.group(1) if name_match else None


def client_home(client: str) -> Path:
    return (Path.home() / DEFAULT_HOME_SUFFIXES[client]).resolve()


def skill_target(config: SkillConfig, client: str) -> Path:
    target = client_home(client) / "skills" / config.name
    if target.name != config.name or target.parent.name != "skills":
        raise SyncError(f"refusing an unsafe skill target for {client}")
    return target


def list_skill(config: SkillConfig, target: ClientTarget) -> Outcome:
    destination = skill_target(config, target.name)
    if destination.is_symlink():
        logging.error(
            "client=%s skill=%s action=list state=invalid",
            target.name,
            config.name,
        )
        return Outcome(
            target.name, "failed", f"managed skill target is a symlink: {destination}"
        )
    if destination.is_dir() and (destination / "SKILL.md").is_file():
        logging.info(
            "client=%s skill=%s action=list state=installed",
            target.name,
            config.name,
        )
        return Outcome(target.name, "installed", str(destination))
    if destination.exists():
        logging.error(
            "client=%s skill=%s action=list state=invalid",
            target.name,
            config.name,
        )
        return Outcome(
            target.name,
            "failed",
            f"managed skill target is not a skill directory: {destination}",
        )
    logging.info(
        "client=%s skill=%s action=list state=absent",
        target.name,
        config.name,
    )
    return Outcome(target.name, "absent", str(destination))


def apply_skill(config: SkillConfig, target: ClientTarget, dry_run: bool) -> Outcome:
    destination = skill_target(config, target.name)
    logging.info(
        "client=%s skill=%s action=publish skill leaf directory destination=%s",
        target.name,
        config.name,
        destination,
    )
    if dry_run:
        return Outcome(
            target.name, "planned", str(destination), ["publish skill leaf directory"]
        )
    try:
        replace_skill_leaf(config.source, destination)
    except SyncError as error:
        return Outcome(
            target.name, "failed", str(error), ["publish skill leaf directory"]
        )
    return Outcome(
        target.name, "applied", str(destination), ["publish skill leaf directory"]
    )


def remove_skill(config: SkillConfig, target: ClientTarget, dry_run: bool) -> Outcome:
    destination = skill_target(config, target.name)
    if destination.is_symlink():
        return Outcome(
            target.name, "failed", f"refusing to remove a symlink: {destination}"
        )
    if not destination.exists():
        return Outcome(target.name, "absent", str(destination))
    if not destination.is_dir():
        return Outcome(
            target.name,
            "failed",
            f"managed skill target is not a directory: {destination}",
        )
    if dry_run:
        logging.info(
            "client=%s skill=%s action=remove skill leaf directory destination=%s",
            target.name,
            config.name,
            destination,
        )
        return Outcome(
            target.name, "planned", str(destination), ["remove skill leaf directory"]
        )
    try:
        logging.info(
            "client=%s skill=%s action=remove skill leaf directory destination=%s",
            target.name,
            config.name,
            destination,
        )
        shutil.rmtree(destination)
    except OSError:
        return Outcome(
            target.name,
            "failed",
            f"could not remove skill directory: {destination}",
            ["remove skill leaf directory"],
        )
    return Outcome(
        target.name, "removed", str(destination), ["remove skill leaf directory"]
    )


def replace_skill_leaf(source: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise SyncError(f"refusing to replace a symlink: {destination}")
    if same_path(source, destination):
        raise SyncError("skill source and destination must be different directories")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{destination.name}.staging-{token}"
    backup = parent / f".{destination.name}.backup-{token}"
    try:
        shutil.copytree(source, staging, symlinks=True)
        if destination.exists():
            destination.rename(backup)
        staging.rename(destination)
    except OSError as error:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise SyncError(f"could not publish skill directory: {destination}") from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if backup.exists():
        shutil.rmtree(backup)


def records(payload: Any, field: str) -> list[dict[str, Any]]:
    values = (
        payload
        if isinstance(payload, list)
        else payload.get(field)
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(values, list) or not all(
        isinstance(item, dict) for item in values
    ):
        raise SyncError(
            f"unexpected plugin-list JSON: expected an array of records in {field}"
        )
    return values


def exact_record(
    records_to_search: Iterable[dict[str, Any]], field: str, expected: str
) -> dict[str, Any] | None:
    matches = [item for item in records_to_search if item.get(field) == expected]
    if len(matches) > 1:
        raise SyncError(
            f"plugin CLI returned more than one record with {field}={expected}"
        )
    return matches[0] if matches else None


def has_field_value(
    records_to_search: Iterable[dict[str, Any]], field: str, expected: str
) -> bool:
    return exact_record(records_to_search, field, expected) is not None


def same_path(left: Path | str, right: Path | str) -> bool:
    def normalize(value: Path | str) -> str:
        text = str(value)
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return os.path.normcase(os.path.normpath(text))

    return normalize(left) == normalize(right)


def action_labels(plan: ClientPlan) -> list[str]:
    return [command.label for command in plan.commands]


def describe_actions(actions: Sequence[str]) -> str:
    return ", ".join(actions) if actions else "no changes"


def outcome_for_plan(plan: ClientPlan, status: str) -> Outcome:
    return Outcome(
        plan.client,
        status,
        f"plugin {plan.selector} prepared",
        action_labels(plan),
    )


if __name__ == "__main__":
    raise SystemExit(main())
