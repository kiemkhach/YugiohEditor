from __future__ import annotations

import os

MEBIBYTE = 1024 * 1024
DEFAULT_MEMORY_PER_WORKER_BYTES = 64 * MEBIBYTE
MINIMUM_MEMORY_RESERVE_BYTES = 512 * MEBIBYTE


def estimate_available_memory_bytes() -> int | None:
    """Return currently available memory without requiring an optional package."""

    try:
        import psutil  # type: ignore[import-not-found]

        available = int(psutil.virtual_memory().available)
        if available > 0:
            return available
    except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass

    if os.name == "nt":
        available = _windows_available_memory_bytes()
        if available is not None:
            return available

    try:
        pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available = pages * page_size
        return available if available > 0 else None
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def select_worker_count(
    item_count: int,
    *,
    hard_cap: int,
    fallback_cap: int,
    available_memory_bytes: int | None,
    memory_per_worker_bytes: int = DEFAULT_MEMORY_PER_WORKER_BYTES,
) -> int:
    """Select a small bounded worker count from work size and available RAM."""

    if item_count <= 0:
        return 0
    if hard_cap <= 0 or fallback_cap <= 0 or memory_per_worker_bytes <= 0:
        raise ValueError("Worker limits and memory budget must be positive.")
    if available_memory_bytes is None:
        memory_limit = fallback_cap
    else:
        available = max(0, int(available_memory_bytes))
        reserve = max(MINIMUM_MEMORY_RESERVE_BYTES, available // 4)
        usable = max(0, available - reserve)
        memory_limit = max(1, usable // memory_per_worker_bytes)
    return max(1, min(int(item_count), hard_cap, memory_limit))


def select_bulk_suggest_worker_count(
    candidate_count: int,
    available_memory_bytes: int | None = None,
    cpu_count: int | None = None,
) -> int:
    """Choose Bulk Suggest concurrency; CPU count is intentionally not a limit."""

    del cpu_count
    return select_worker_count(
        candidate_count,
        hard_cap=8,
        fallback_cap=4,
        available_memory_bytes=available_memory_bytes,
    )


def _windows_available_memory_bytes() -> int | None:
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = (
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            )

        status = MemoryStatusEx()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        available = int(status.available_physical)
        return available if available > 0 else None
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
