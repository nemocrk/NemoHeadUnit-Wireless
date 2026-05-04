"""
NemoHeadUnit-Wireless v2 — config_schema

Lightweight schema descriptor for module configuration keys.

Usage (inside a module):

    from shared.config_schema import field_string, field_int, field_float, field_enum

    _SCHEMA = {
        "pin":    field_string(default="1234"),
        "volume": field_int(default=80, min=0, max=100),
        "gain":   field_float(default=1.0, min=0.0, max=2.0),
        "mode":   field_enum(default="auto", choices=["off", "auto", "on"]),
    }

Pass _SCHEMA to cfg.get():

    cfg.get(defaults=_DEFAULTS, schema=_SCHEMA)

config_manager stores the schema in RAM and echoes it in every
config.response so config_ui can render the correct widget.

Validation
----------
validate_value(field, value) → raises ValueError on mismatch.
config_manager calls this in on_config_set before persisting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Schema descriptor
# ---------------------------------------------------------------------------

@dataclass
class ConfigFieldSchema:
    type:    Literal["int", "float", "string", "enum"]
    default: object

    # int / float only
    min: int | float | None = None
    max: int | float | None = None

    # enum only
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for bus payloads."""
        d: dict = {"type": self.type, "default": self.default}
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.choices:
            d["choices"] = list(self.choices)
        return d

    @staticmethod
    def from_dict(d: dict) -> "ConfigFieldSchema":
        """Deserialise from a plain dict (received via bus payload)."""
        return ConfigFieldSchema(
            type=d["type"],
            default=d.get("default"),
            min=d.get("min"),
            max=d.get("max"),
            choices=d.get("choices", []),
        )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------

def field_string(default: str = "") -> ConfigFieldSchema:
    return ConfigFieldSchema(type="string", default=default)


def field_int(
    default: int = 0,
    min: int | None = None,
    max: int | None = None,
) -> ConfigFieldSchema:
    return ConfigFieldSchema(type="int", default=default, min=min, max=max)


def field_float(
    default: float = 0.0,
    min: float | None = None,
    max: float | None = None,
) -> ConfigFieldSchema:
    return ConfigFieldSchema(type="float", default=default, min=min, max=max)


def field_enum(default: str, choices: list[str]) -> ConfigFieldSchema:
    if default not in choices:
        raise ValueError(
            f"field_enum: default {default!r} is not in choices {choices}"
        )
    return ConfigFieldSchema(type="enum", default=default, choices=choices)


# ---------------------------------------------------------------------------
# Schema serialisation helpers
# ---------------------------------------------------------------------------

def schema_to_dict(schema: dict[str, ConfigFieldSchema]) -> dict:
    """Convert a full module schema to a bus-safe plain dict."""
    return {k: v.to_dict() for k, v in schema.items()}


def schema_from_dict(d: dict) -> dict[str, ConfigFieldSchema]:
    """Restore a full module schema from a bus payload dict."""
    return {k: ConfigFieldSchema.from_dict(v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_value(schema_field: ConfigFieldSchema, value: object) -> object:
    """
    Validate *value* against *schema_field*.

    Returns the (possibly coerced) value on success.
    Raises ValueError with a human-readable message on failure.

    Coercion rules
    --------------
    - int/float: str → int/float (tolerates values coming as strings from QLineEdit)
    - string:    value is str-cast
    - enum:      value must be one of choices (str comparison)
    """
    t = schema_field.type

    if t == "string":
        return str(value)

    if t == "enum":
        str_value = str(value)
        if str_value not in schema_field.choices:
            raise ValueError(
                f"expected one of {schema_field.choices}, got {value!r}"
            )
        return str_value

    if t == "int":
        try:
            coerced = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"expected int, got {type(value).__name__} ({value!r})")
        if schema_field.min is not None and coerced < schema_field.min:
            raise ValueError(f"{coerced} is below minimum {schema_field.min}")
        if schema_field.max is not None and coerced > schema_field.max:
            raise ValueError(f"{coerced} is above maximum {schema_field.max}")
        return coerced

    if t == "float":
        try:
            coerced = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"expected float, got {type(value).__name__} ({value!r})")
        if schema_field.min is not None and coerced < schema_field.min:
            raise ValueError(f"{coerced} is below minimum {schema_field.min}")
        if schema_field.max is not None and coerced > schema_field.max:
            raise ValueError(f"{coerced} is above maximum {schema_field.max}")
        return coerced

    raise ValueError(f"unknown schema type {t!r}")
