"""
Web Browser Head Unit — Configuration Schema Descriptor

Lightweight schema descriptors and validation for module configuration keys.

Usage inside a module:

    from shared.config_schema import field_string, field_int, field_float, field_enum, field_bool

    def get_schema(self) -> dict:
        return {
            "pin":     field_string(default="1234"),
            "volume":  field_int(default=80, min=0, max=100),
            "gain":    field_float(default=1.0, min=0.0, max=2.0),
            "mode":    field_enum(default="auto", choices=["off", "auto", "on"]),
            "enabled": field_bool(default=True),
        }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}

AnyFieldSchema = Any


@dataclass
class ConfigFieldSchema:
    type: Literal["int", "float", "string", "enum", "bool"]
    default: object

    min: int | float | None = None
    max: int | float | None = None
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
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
        return ConfigFieldSchema(
            type=d["type"],
            default=d.get("default"),
            min=d.get("min"),
            max=d.get("max"),
            choices=d.get("choices", []),
        )


@dataclass
class ConfigFieldMessage:
    fields: dict[str, AnyFieldSchema]
    optional: bool = False

    def to_dict(self) -> dict:
        return {
            "type": "message",
            "optional": self.optional,
            "fields": {k: _node_to_dict(v) for k, v in self.fields.items()},
        }

    @staticmethod
    def from_dict(d: dict) -> "ConfigFieldMessage":
        return ConfigFieldMessage(
            fields={k: _node_from_dict(v) for k, v in d.get("fields", {}).items()},
            optional=d.get("optional", False),
        )


@dataclass
class ConfigFieldList:
    item_schema: AnyFieldSchema
    default: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": "list",
            "item_schema": _node_to_dict(self.item_schema),
            "default": self.default,
        }

    @staticmethod
    def from_dict(d: dict) -> "ConfigFieldList":
        return ConfigFieldList(
            item_schema=_node_from_dict(d["item_schema"]),
            default=d.get("default", []),
        )


@dataclass
class ConfigFieldOneof:
    branches: dict[str, AnyFieldSchema]
    active_branch: str

    def __post_init__(self) -> None:
        if self.active_branch not in self.branches:
            raise ValueError(
                f"ConfigFieldOneof: active_branch {self.active_branch!r} is not in branches {list(self.branches)}"
            )

    def to_dict(self) -> dict:
        return {
            "type": "oneof",
            "active_branch": self.active_branch,
            "branches": {k: _node_to_dict(v) for k, v in self.branches.items()},
        }

    @staticmethod
    def from_dict(d: dict) -> "ConfigFieldOneof":
        return ConfigFieldOneof(
            branches={k: _node_from_dict(v) for k, v in d.get("branches", {}).items()},
            active_branch=d["active_branch"],
        )


def _node_to_dict(node: AnyFieldSchema) -> dict:
    if isinstance(node, (ConfigFieldMessage, ConfigFieldList, ConfigFieldOneof, ConfigFieldSchema)):
        return node.to_dict()
    raise TypeError(f"_node_to_dict: unsupported schema node type {type(node)!r}")


def _node_from_dict(d: dict) -> AnyFieldSchema:
    t = d.get("type")
    if t == "message":
        return ConfigFieldMessage.from_dict(d)
    if t == "list":
        return ConfigFieldList.from_dict(d)
    if t == "oneof":
        return ConfigFieldOneof.from_dict(d)
    return ConfigFieldSchema.from_dict(d)


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
        raise ValueError(f"field_enum: default {default!r} is not in choices {choices}")
    return ConfigFieldSchema(type="enum", default=default, choices=choices)


def field_bool(default: bool = False) -> ConfigFieldSchema:
    return ConfigFieldSchema(type="bool", default=bool(default))


def schema_to_dict(schema: dict[str, AnyFieldSchema]) -> dict:
    return {k: _node_to_dict(v) for k, v in schema.items()}


def schema_from_dict(d: dict) -> dict[str, AnyFieldSchema]:
    return {k: _node_from_dict(v) for k, v in d.items()}


def validate_value(schema_field: ConfigFieldSchema, value: object) -> object:
    t = schema_field.type

    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        str_val = str(value).strip().lower()
        if str_val in _BOOL_TRUE:
            return True
        if str_val in _BOOL_FALSE:
            return False
        raise ValueError(f"expected bool (true/false/yes/no/on/off/1/0), got {value!r}")

    if t == "string":
        return str(value)

    if t == "enum":
        str_value = str(value)
        if str_value not in schema_field.choices:
            raise ValueError(f"expected one of {schema_field.choices}, got {value!r}")
        return str_value

    if t == "int":
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"expected int, got {type(value).__name__} ({value!r})")
        if schema_field.min is not None and coerced < schema_field.min:
            raise ValueError(f"{coerced} is below minimum {schema_field.min}")
        if schema_field.max is not None and coerced > schema_field.max:
            raise ValueError(f"{coerced} is above maximum {schema_field.max}")
        return coerced

    if t == "float":
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"expected float, got {type(value).__name__} ({value!r})")
        if schema_field.min is not None and coerced < schema_field.min:
            raise ValueError(f"{coerced} is below minimum {schema_field.min}")
        if schema_field.max is not None and coerced > schema_field.max:
            raise ValueError(f"{coerced} is above maximum {schema_field.max}")
        return coerced

    raise ValueError(f"unknown schema type {t!r}")
