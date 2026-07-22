from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from re import Pattern
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yugioh_editor.repositories.game.repository import GameRepository


@dataclass(frozen=True, slots=True)
class RuleMethodCall:
    method_name: str
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SubfileRule:
    source_pattern: str
    compiled_pattern: Pattern[str]
    codec_name: str
    decode_params: Mapping[str, Any]
    encode_params: Mapping[str, Any]
    virtual: bool = False
    table_name: str | None = None
    table_parameters: tuple[str, ...] = ()
    editor_columns: tuple[str, ...] = ()
    pre_decode: tuple[RuleMethodCall, ...] = ()
    post_decode: tuple[RuleMethodCall, ...] = ()
    pre_encode: tuple[RuleMethodCall, ...] = ()
    post_encode: tuple[RuleMethodCall, ...] = ()


@dataclass(slots=True)
class RuleProcessingContext:
    repository: "GameRepository"
    rule: SubfileRule
    relative_path: str
    language: str | None
    decode_params: dict[str, Any]
    encode_params: dict[str, Any]
    metadata: dict[str, Any]


def deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(deep_freeze(item) for item in value)
    return deepcopy(value)


def deep_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {deep_thaw(item) for item in value}
    return deepcopy(value)
