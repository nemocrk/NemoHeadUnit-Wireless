import pytest
from shared.config_schema import (
    ConfigFieldSchema,
    ConfigFieldMessage,
    ConfigFieldList,
    ConfigFieldOneof,
    field_string,
    field_int,
    field_float,
    field_enum,
    field_bool,
    schema_to_dict,
    schema_from_dict,
    validate_value,
)

pytestmark = pytest.mark.unit


def test_schema_field_factories():
    s = field_string("default_val")
    assert s.type == "string" and s.default == "default_val"

    i = field_int(10, min=0, max=100)
    assert i.type == "int" and i.min == 0 and i.max == 100

    f = field_float(1.5, min=0.0, max=5.0)
    assert f.type == "float" and f.default == 1.5

    b = field_bool(True)
    assert b.type == "bool" and b.default is True

    e = field_enum("a", ["a", "b", "c"])
    assert e.type == "enum" and e.choices == ["a", "b", "c"]

    with pytest.raises(ValueError):
        field_enum("invalid", ["a", "b"])


def test_schema_serialization_roundtrip():
    schema = {
        "text": field_string("hello"),
        "nested": ConfigFieldMessage(fields={"count": field_int(5)}),
        "items": ConfigFieldList(item_schema=field_string("item")),
        "choice": ConfigFieldOneof(
            branches={"b1": field_string("val1"), "b2": field_int(2)},
            active_branch="b1",
        ),
    }
    serialized = schema_to_dict(schema)
    restored = schema_from_dict(serialized)
    assert restored["text"].default == "hello"
    assert restored["nested"].fields["count"].default == 5
    assert restored["choice"].active_branch == "b1"


def test_validate_value_bool():
    schema = field_bool(False)
    assert validate_value(schema, True) is True
    assert validate_value(schema, "yes") is True
    assert validate_value(schema, "0") is False
    assert validate_value(schema, "off") is False
    with pytest.raises(ValueError):
        validate_value(schema, "not_a_bool")


def test_validate_value_int_bounds():
    schema = field_int(10, min=5, max=15)
    assert validate_value(schema, "10") == 10
    with pytest.raises(ValueError):
        validate_value(schema, 4)
    with pytest.raises(ValueError):
        validate_value(schema, 16)
    with pytest.raises(ValueError):
        validate_value(schema, "abc")


def test_validate_value_enum():
    schema = field_enum("foo", ["foo", "bar"])
    assert validate_value(schema, "bar") == "bar"
    with pytest.raises(ValueError):
        validate_value(schema, "baz")
