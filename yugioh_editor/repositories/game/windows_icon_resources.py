"""Validate ICO files and replace staged Windows executable icon resources."""

from __future__ import annotations

import ctypes
import struct
import sys
import warnings
from ctypes import wintypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image

_RT_ICON = 3
_RT_GROUP_ICON = 14
_LANG_NEUTRAL = 0
_LOAD_LIBRARY_AS_DATAFILE = 0x00000002
_LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020
_MISSING_RESOURCE_ERRORS = frozenset({1812, 1813, 1814, 1815})
_MAX_RESOURCE_ID = 0xFFFF

_ICON_DIRECTORY = struct.Struct("<HHH")
_ICON_ENTRY = struct.Struct("<BBBBHHII")
_GROUP_ICON_ENTRY = struct.Struct("<BBBBHHIH")

ResourceName = int | str


@dataclass(frozen=True)
class _IconImage:
    width: int
    height: int
    color_count: int
    reserved: int
    planes: int
    bit_count: int
    payload: bytes


@dataclass(frozen=True)
class _GroupResource:
    name: ResourceName
    language: int


@dataclass(frozen=True)
class _ResourceInventory:
    groups: tuple[_GroupResource, ...]
    icon_ids: frozenset[int]


class _ResourceApi(Protocol):
    def inspect(self, path: Path) -> _ResourceInventory: ...

    def begin_update(self, path: Path) -> object: ...

    def update_resource(
        self,
        handle: object,
        resource_type: int,
        name: ResourceName,
        language: int,
        data: bytes,
    ) -> None: ...

    def end_update(self, handle: object, *, discard: bool) -> None: ...


def validate_icon_data(data: bytes) -> None:
    """Raise ``ValueError`` unless *data* is a bounded, standard ICO file."""

    _parse_icon_data(data)


def update_executable_icon(path: str | Path, icon_data: bytes) -> None:
    """Replace all icon groups in a staged PE executable with *icon_data*.

    Existing icon image resources are deliberately retained. New image resource
    IDs are allocated without colliding with any numeric ``RT_ICON`` name.
    """

    images = _parse_icon_data(icon_data)
    api = _create_resource_api()
    target = Path(path)
    inventory = api.inspect(target)
    groups = _groups_or_default(inventory.groups)
    icon_ids = _allocate_icon_ids(len(images), inventory.icon_ids)
    group_data = _build_group_icon_data(images, icon_ids)

    handle = api.begin_update(target)
    try:
        for language in _unique_languages(groups):
            for icon_id, image in zip(icon_ids, images, strict=True):
                api.update_resource(
                    handle,
                    _RT_ICON,
                    icon_id,
                    language,
                    image.payload,
                )
        for group in groups:
            api.update_resource(
                handle,
                _RT_GROUP_ICON,
                group.name,
                group.language,
                group_data,
            )
        api.end_update(handle, discard=False)
    except BaseException as error:
        try:
            api.end_update(handle, discard=True)
        except Exception as discard_error:
            error.add_note(
                f"Discarding the PE resource update also failed: {discard_error}"
            )
        raise


def _parse_icon_data(data: bytes) -> tuple[_IconImage, ...]:
    if not isinstance(data, bytes):
        raise TypeError("ICO data must be bytes")
    if len(data) < _ICON_DIRECTORY.size:
        raise ValueError("ICO header is truncated")

    reserved, icon_type, image_count = _ICON_DIRECTORY.unpack_from(data)
    if reserved != 0:
        raise ValueError("ICO reserved field must be zero")
    if icon_type != 1:
        raise ValueError("ICO type must be 1")
    if image_count == 0:
        raise ValueError("ICO must contain at least one image")

    directory_end = _ICON_DIRECTORY.size + image_count * _ICON_ENTRY.size
    if directory_end > len(data):
        raise ValueError("ICO directory entries are truncated")

    images: list[_IconImage] = []
    payload_ranges: list[tuple[int, int]] = []
    for index in range(image_count):
        entry_offset = _ICON_DIRECTORY.size + index * _ICON_ENTRY.size
        (
            width,
            height,
            color_count,
            entry_reserved,
            planes,
            bit_count,
            payload_size,
            payload_offset,
        ) = _ICON_ENTRY.unpack_from(data, entry_offset)
        if entry_reserved != 0:
            raise ValueError(f"ICO entry {index} reserved field must be zero")
        if payload_size == 0:
            raise ValueError(f"ICO entry {index} has an empty payload")
        if payload_offset < directory_end:
            raise ValueError(f"ICO entry {index} payload overlaps its directory")
        if payload_offset > len(data) or payload_size > len(data) - payload_offset:
            raise ValueError(f"ICO entry {index} payload is out of bounds")

        payload_end = payload_offset + payload_size
        payload_ranges.append((payload_offset, payload_end))
        images.append(
            _IconImage(
                width=width,
                height=height,
                color_count=color_count,
                reserved=entry_reserved,
                planes=planes,
                bit_count=bit_count,
                payload=data[payload_offset:payload_end],
            )
        )

    previous_end = directory_end
    for payload_start, payload_end in sorted(payload_ranges):
        if payload_start < previous_end:
            raise ValueError("ICO image payloads overlap")
        previous_end = payload_end
    for index, image in enumerate(images):
        _validate_icon_image(index, image)
    return tuple(images)


def _validate_icon_image(index: int, image: _IconImage) -> None:
    """Decode one original payload through Pillow's ICO reader.

    Rebuilding only the directory wrapper lets Pillow validate both supported
    ICO payload forms (PNG and DIB) without re-encoding or otherwise changing
    the bytes later written to ``RT_ICON``.
    """

    payload_offset = _ICON_DIRECTORY.size + _ICON_ENTRY.size
    single_image_icon = (
        _ICON_DIRECTORY.pack(0, 1, 1)
        + _ICON_ENTRY.pack(
            image.width,
            image.height,
            image.color_count,
            image.reserved,
            image.planes,
            image.bit_count,
            len(image.payload),
            payload_offset,
        )
        + image.payload
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with Image.open(BytesIO(single_image_icon), formats=("ICO",)) as decoded:
                decoded.load()
                expected_size = (image.width or 256, image.height or 256)
                if decoded.size != expected_size:
                    raise ValueError(
                        f"decoded size {decoded.size} does not match {expected_size}"
                    )
    except Exception as error:
        raise ValueError(
            f"ICO entry {index} does not contain valid image data"
        ) from error


def _groups_or_default(
    groups: tuple[_GroupResource, ...],
) -> tuple[_GroupResource, ...]:
    if groups:
        return groups
    return (_GroupResource(name=1, language=_LANG_NEUTRAL),)


def _unique_languages(groups: tuple[_GroupResource, ...]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(group.language for group in groups))


def _allocate_icon_ids(count: int, existing_ids: frozenset[int]) -> tuple[int, ...]:
    allocated: list[int] = []
    candidate = 1
    while len(allocated) < count and candidate <= _MAX_RESOURCE_ID:
        if candidate not in existing_ids:
            allocated.append(candidate)
        candidate += 1
    if len(allocated) != count:
        raise ValueError("The executable has too few free numeric RT_ICON IDs")
    return tuple(allocated)


def _build_group_icon_data(
    images: tuple[_IconImage, ...],
    icon_ids: tuple[int, ...],
) -> bytes:
    if len(images) != len(icon_ids):
        raise ValueError("Each icon image must have one resource ID")
    output = bytearray(_ICON_DIRECTORY.pack(0, 1, len(images)))
    for image, icon_id in zip(images, icon_ids, strict=True):
        output.extend(
            _GROUP_ICON_ENTRY.pack(
                image.width,
                image.height,
                image.color_count,
                image.reserved,
                image.planes,
                image.bit_count,
                len(image.payload),
                icon_id,
            )
        )
    return bytes(output)


def _create_resource_api() -> _ResourceApi:
    if sys.platform != "win32":
        raise OSError("Windows PE icon updates are only supported on Windows")
    return _WindowsResourceApi()


class _WindowsResourceApi:
    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self._name_callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMODULE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.LPARAM,
        )
        self._language_callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMODULE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.WORD,
            wintypes.LPARAM,
        )

        kernel32.LoadLibraryExW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.HANDLE,
            wintypes.DWORD,
        )
        kernel32.LoadLibraryExW.restype = wintypes.HMODULE
        kernel32.FreeLibrary.argtypes = (wintypes.HMODULE,)
        kernel32.FreeLibrary.restype = wintypes.BOOL
        kernel32.EnumResourceNamesW.argtypes = (
            wintypes.HMODULE,
            ctypes.c_void_p,
            self._name_callback_type,
            wintypes.LPARAM,
        )
        kernel32.EnumResourceNamesW.restype = wintypes.BOOL
        kernel32.EnumResourceLanguagesW.argtypes = (
            wintypes.HMODULE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            self._language_callback_type,
            wintypes.LPARAM,
        )
        kernel32.EnumResourceLanguagesW.restype = wintypes.BOOL
        kernel32.BeginUpdateResourceW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.BOOL,
        )
        kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
        kernel32.UpdateResourceW.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.UpdateResourceW.restype = wintypes.BOOL
        kernel32.EndUpdateResourceW.argtypes = (
            wintypes.HANDLE,
            wintypes.BOOL,
        )
        kernel32.EndUpdateResourceW.restype = wintypes.BOOL

    def inspect(self, path: Path) -> _ResourceInventory:
        flags = _LOAD_LIBRARY_AS_DATAFILE | _LOAD_LIBRARY_AS_IMAGE_RESOURCE
        module = self._kernel32.LoadLibraryExW(str(path), None, flags)
        if not module:
            self._raise_last_error(f"LoadLibraryExW({path})")
        try:
            group_names = self._enumerate_names(module, _RT_GROUP_ICON)
            groups = tuple(
                _GroupResource(name=name, language=language)
                for name in group_names
                for language in self._enumerate_languages(
                    module,
                    _RT_GROUP_ICON,
                    name,
                )
            )
            icon_ids = frozenset(
                name
                for name in self._enumerate_names(module, _RT_ICON)
                if isinstance(name, int)
            )
            return _ResourceInventory(groups=groups, icon_ids=icon_ids)
        finally:
            if not self._kernel32.FreeLibrary(module) and sys.exc_info()[0] is None:
                self._raise_last_error("FreeLibrary")

    def begin_update(self, path: Path) -> object:
        handle = self._kernel32.BeginUpdateResourceW(str(path), False)
        if not handle:
            self._raise_last_error(f"BeginUpdateResourceW({path})")
        return handle

    def update_resource(
        self,
        handle: object,
        resource_type: int,
        name: ResourceName,
        language: int,
        data: bytes,
    ) -> None:
        if resource_type not in {_RT_ICON, _RT_GROUP_ICON}:
            raise ValueError("Only RT_ICON and RT_GROUP_ICON resources may be updated")
        if not data:
            raise ValueError("Resource data must not be empty")
        if not 0 <= language <= _MAX_RESOURCE_ID:
            raise ValueError("Resource language must fit an unsigned 16-bit value")

        type_pointer, type_buffer = self._identifier_pointer(resource_type)
        name_pointer, name_buffer = self._identifier_pointer(name)
        data_buffer = ctypes.create_string_buffer(data, len(data))
        updated = self._kernel32.UpdateResourceW(
            handle,
            type_pointer,
            name_pointer,
            language,
            ctypes.cast(data_buffer, ctypes.c_void_p),
            len(data),
        )
        _ = type_buffer, name_buffer
        if not updated:
            self._raise_last_error(
                f"UpdateResourceW(type={resource_type}, name={name!r})"
            )

    def end_update(self, handle: object, *, discard: bool) -> None:
        if not self._kernel32.EndUpdateResourceW(handle, discard):
            action = "discard" if discard else "commit"
            self._raise_last_error(f"EndUpdateResourceW({action})")

    def _enumerate_names(
        self,
        module: object,
        resource_type: int,
    ) -> tuple[ResourceName, ...]:
        names: list[ResourceName] = []
        callback_errors: list[BaseException] = []

        def collect_name(
            _module: object,
            _type: object,
            name_pointer: object,
            _parameter: int,
        ) -> bool:
            try:
                names.append(self._decode_resource_name(name_pointer))
            except BaseException as error:
                callback_errors.append(error)
                return False
            return True

        callback = self._name_callback_type(collect_name)
        ctypes.set_last_error(0)
        succeeded = self._kernel32.EnumResourceNamesW(
            module,
            ctypes.c_void_p(resource_type),
            callback,
            0,
        )
        self._check_enumeration_result(
            succeeded,
            callback_errors,
            f"EnumResourceNamesW(type={resource_type})",
        )
        return tuple(names)

    def _enumerate_languages(
        self,
        module: object,
        resource_type: int,
        name: ResourceName,
    ) -> tuple[int, ...]:
        languages: list[int] = []
        callback_errors: list[BaseException] = []

        def collect_language(
            _module: object,
            _type: object,
            _name: object,
            language: int,
            _parameter: int,
        ) -> bool:
            try:
                languages.append(int(language))
            except BaseException as error:
                callback_errors.append(error)
                return False
            return True

        callback = self._language_callback_type(collect_language)
        name_pointer, name_buffer = self._identifier_pointer(name)
        ctypes.set_last_error(0)
        succeeded = self._kernel32.EnumResourceLanguagesW(
            module,
            ctypes.c_void_p(resource_type),
            name_pointer,
            callback,
            0,
        )
        _ = name_buffer
        self._check_enumeration_result(
            succeeded,
            callback_errors,
            f"EnumResourceLanguagesW(type={resource_type}, name={name!r})",
        )
        return tuple(languages)

    @staticmethod
    def _check_enumeration_result(
        succeeded: bool,
        callback_errors: list[BaseException],
        action: str,
    ) -> None:
        if callback_errors:
            raise callback_errors[0]
        if succeeded:
            return
        error_code = ctypes.get_last_error()
        if error_code in _MISSING_RESOURCE_ERRORS:
            return
        _WindowsResourceApi._raise_last_error(action)

    @staticmethod
    def _decode_resource_name(pointer: object) -> ResourceName:
        if isinstance(pointer, int):
            pointer_value = pointer
        else:
            pointer_value = ctypes.cast(pointer, ctypes.c_void_p).value or 0
        if pointer_value <= _MAX_RESOURCE_ID:
            return pointer_value
        return ctypes.wstring_at(pointer_value)

    @staticmethod
    def _identifier_pointer(
        identifier: ResourceName,
    ) -> tuple[ctypes.c_void_p, object | None]:
        if isinstance(identifier, bool):
            raise TypeError("Resource identifiers cannot be booleans")
        if isinstance(identifier, int):
            if not 0 <= identifier <= _MAX_RESOURCE_ID:
                raise ValueError("Numeric resource identifiers must fit 16 bits")
            return ctypes.c_void_p(identifier), None
        if not isinstance(identifier, str) or not identifier or "\0" in identifier:
            raise ValueError("String resource identifiers must be non-empty")
        buffer = ctypes.create_unicode_buffer(identifier)
        return ctypes.cast(buffer, ctypes.c_void_p), buffer

    @staticmethod
    def _raise_last_error(action: str) -> None:
        error_code = ctypes.get_last_error()
        if error_code:
            message = ctypes.FormatError(error_code).strip()
            raise OSError(error_code, f"{action} failed: {message}")
        raise OSError(f"{action} failed")


__all__ = ["update_executable_icon", "validate_icon_data"]
