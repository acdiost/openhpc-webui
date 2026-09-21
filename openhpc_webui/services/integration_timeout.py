import os


def bounded_timeout_seconds(
    variable: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 300,
) -> int:
    """Read a bounded positive timeout without allowing invalid config to hang I/O."""
    raw_value = os.getenv(variable, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))
