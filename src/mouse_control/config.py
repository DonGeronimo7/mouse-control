"""TOML configuration loading and saving."""

from __future__ import annotations

import copy
from datetime import date, datetime, time
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_DPI_STAGES = [800, 1500, 2000, 2500, 3000]
DEFAULT_DPI = 800


def get_config_path() -> Path:
    return Path(os.path.expanduser("~/.config/mouse-control/config.toml"))


def generate_config(
    device_info: Any,
    button_mappings: dict[str, str],
    dpi_stages: list[int] | None = None,
    active_dpi: int = DEFAULT_DPI,
    polling_rate_hz: int | None = None,
) -> str:
    """Generate the human-editable TOML configuration."""
    stages = DEFAULT_DPI_STAGES if dpi_stages is None else dpi_stages
    lines = [
        "# mouse-control configuration",
        "# Actions: passthrough, disable, dpi-cycle, mouse:BTN_*, key:KEY_*, chord:KEY_*+KEY_*",
        "",
        "[device]",
        f'name = {device_info.name!r}',
        f'event_path = {device_info.path!r}',
        f'phys = {getattr(device_info, "phys", "")!r}',
    ]

    if device_info.vendor is not None:
        lines.append(f"vendor = {device_info.vendor}")
    if device_info.product is not None:
        lines.append(f"product = {device_info.product}")
    if device_info.bustype is not None:
        lines.append(f"bustype = {device_info.bustype}")

    lines.extend([
        "",
        "[dpi]",
        f"active = {active_dpi}",
        "stages = [" + ", ".join(str(v) for v in stages) + "]",
        "",
        "[polling]",
        f"rate_hz = {polling_rate_hz}" if polling_rate_hz is not None else "# rate_hz = 1000",
        "",
        "[notifications]",
        "dpi_changes = true",
        "",
        "[remap]",
    ])

    for button, action in button_mappings.items():
        lines.append(f'{button!r} = {action!r}')

    return "\n".join(lines) + "\n"


def merge_setup_config(existing: dict[str, Any], device_info: Any, *,
                       mappings: dict[str, str], dpi_stages: list[int],
                       active_dpi: int, polling_rate_hz: int | None) -> str:
    """Merge setup choices without discarding forward-compatible TOML."""
    result = copy.deepcopy(existing)
    device = result.setdefault("device", {})
    if not isinstance(device, dict):
        raise ValueError("Existing [device] configuration is invalid")
    device.update({"name": device_info.name, "event_path": device_info.path,
                   "phys": getattr(device_info, "phys", "")})
    for key in ("vendor", "product", "bustype"):
        value = getattr(device_info, key)
        if value is not None:
            device[key] = value
    dpi = result.setdefault("dpi", {})
    if not isinstance(dpi, dict):
        raise ValueError("Existing [dpi] configuration is invalid")
    dpi.update({"active": active_dpi, "stages": list(dpi_stages)})
    polling = result.setdefault("polling", {})
    if not isinstance(polling, dict):
        raise ValueError("Existing [polling] configuration is invalid")
    if polling_rate_hz is not None:
        polling["rate_hz"] = polling_rate_hz
    notifications = result.setdefault("notifications", {})
    if not isinstance(notifications, dict):
        raise ValueError("Existing [notifications] configuration is invalid")
    notifications.setdefault("dpi_changes", True)
    result["remap"] = dict(mappings)
    return _dump_toml(result)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_toml_key(key)} = {_toml_value(item)}" for key, item in value.items()
        ) + " }"
    raise ValueError(f"Unsupported TOML value in setup configuration: {value!r}")


def _toml_key(key: str) -> str:
    return key if key.replace("_", "").isalnum() else json.dumps(key)


def _dump_toml(data: dict[str, Any]) -> str:
    """Write parsed TOML while retaining unknown nested tables."""
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a table")
    lines = ["# mouse-control configuration"]

    def write_table(values: dict[str, Any], path: list[str] | None = None) -> None:
        scalar_items = [(key, value) for key, value in values.items()
                        if not isinstance(value, dict)]
        if path is not None:
            lines.extend(["", "[" + ".".join(_toml_key(key) for key in path) + "]"])
        elif scalar_items:
            lines.append("")
        lines.extend(f"{_toml_key(key)} = {_toml_value(value)}"
                     for key, value in scalar_items)
        for key, value in values.items():
            if isinstance(value, dict):
                write_table(value, [*path, key] if path else [key])

    write_table(data)
    return "\n".join(lines) + "\n"


def save_config(config_content: str) -> Path:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(config_content, encoding="utf-8")
    tmp.replace(path)
    return path


def load_config(path: Path | None = None) -> dict[str, Any]:
    import tomllib

    config_path = path or get_config_path()
    with config_path.open("rb") as handle:
        return tomllib.load(handle)
