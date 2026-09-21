"""Secret-safe command line diagnostics for application configuration."""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import SecretStr, ValidationError

from apps.api.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class ConfigurationCheck:
    """Presence result for one configuration variable."""

    name: str
    present: bool
    required: bool


def _secret_is_present(value: SecretStr | None) -> bool:
    return value is not None and bool(value.get_secret_value().strip())


def inspect_configuration(settings: Settings) -> tuple[ConfigurationCheck, ...]:
    """Inspect only presence; never return or render credential values."""

    return (
        ConfigurationCheck("DATABASE_URL", _secret_is_present(settings.database_url), True),
        ConfigurationCheck("OPENAI_API_KEY", _secret_is_present(settings.openai_api_key), False),
        ConfigurationCheck(
            "DEEPSEEK_API_KEY", _secret_is_present(settings.deepseek_api_key), False
        ),
        ConfigurationCheck("PENGUIN_API_KEY", _secret_is_present(settings.penguin_api_key), False),
        ConfigurationCheck(
            "AMAP_WEB_SERVICE_KEY", _secret_is_present(settings.amap_web_service_key), False
        ),
        ConfigurationCheck("AMAP_JS_KEY", _secret_is_present(settings.amap_js_key), False),
        ConfigurationCheck(
            "QWEATHER_API_KEY", _secret_is_present(settings.qweather_api_key), False
        ),
    )


def render_doctor(settings: Settings) -> str:
    """Build the deterministic, redacted output for ``config doctor``."""

    checks = inspect_configuration(settings)
    lines = [f"Configuration doctor ({settings.app_env})"]
    for check in checks:
        state = "present" if check.present else "missing"
        necessity = "required" if check.required else "optional"
        lines.append(f"{check.name}: {state} ({necessity})")
    ready = all(check.present for check in checks if check.required)
    lines.append(f"Result: {'ready' if ready else 'blocked'}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create the small configuration command parser."""

    parser = argparse.ArgumentParser(prog="config", description="Configuration diagnostics")
    parser.add_subparsers(dest="command", required=True).add_parser(
        "doctor", help="show presence of required and optional settings"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested configuration diagnostic."""

    arguments = build_parser().parse_args(argv)
    if arguments.command == "doctor":
        try:
            settings = get_settings()
        except ValidationError:
            print("Configuration invalid; check variable types and required fields.")
            return 2
        print(render_doctor(settings))
        checks = inspect_configuration(settings)
        return 0 if all(check.present for check in checks if check.required) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
