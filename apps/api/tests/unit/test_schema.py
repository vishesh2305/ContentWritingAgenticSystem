from pydantic import BaseModel, Field

from content_agents.llm.schema import find_unbounded_arrays


class Bounded(BaseModel):
    tags: list[str] = Field(max_length=5)


class Unbounded(BaseModel):
    tags: list[str]


class Inner(BaseModel):
    items: list[int]


class Outer(BaseModel):
    inner: Inner
    names: list[str] = Field(max_length=3)


class Matrix(BaseModel):
    rows: list[list[int]] = Field(max_length=3)


def test_bounded_list_passes() -> None:
    assert find_unbounded_arrays(Bounded.model_json_schema()) == []


def test_unbounded_top_level_list_is_reported() -> None:
    assert find_unbounded_arrays(Unbounded.model_json_schema()) == ["properties.tags"]


def test_unbounded_list_inside_nested_model_is_reported() -> None:
    assert find_unbounded_arrays(Outer.model_json_schema()) == ["$defs.Inner.properties.items"]


def test_inner_list_of_a_bounded_list_must_also_be_bounded() -> None:
    assert find_unbounded_arrays(Matrix.model_json_schema()) == ["properties.rows.items"]
