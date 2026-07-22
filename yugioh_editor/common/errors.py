class YugiohEditorError(Exception):
    """Base application exception."""


class InvalidFileFormatError(YugiohEditorError):
    """Raised when binary data does not match the expected game format."""


class UnsupportedFileError(YugiohEditorError):
    """Raised when a file type is not supported by the selected operation."""


class ProjectValidationError(YugiohEditorError):
    """Raised when a project or source game folder is incomplete."""


class RulePipelineError(RuntimeError):
    """Raised when a configured rule-processing step fails."""

    def __init__(
        self,
        message: str,
        *,
        resource: str,
        pattern: str,
        codec: str,
        virtual: bool,
        phase: str,
        step: int,
        method: str,
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.pattern = pattern
        self.codec = codec
        self.virtual = virtual
        self.phase = phase
        self.step = step
        self.method = method


class PackResourceError(YugiohEditorError):
    """Add source-file and rule context to a resource packing failure."""

    def __init__(
        self,
        *,
        source_file: str,
        resource: str,
        pattern: str,
        codec: str,
        virtual: bool,
        phase: str,
        step: int | None,
        method: str,
        cause: Exception,
    ) -> None:
        self.source_file = source_file
        self.resource = resource
        self.pattern = pattern
        self.codec = codec
        self.virtual = virtual
        self.phase = phase
        self.step = step
        self.method = method
        step_value = "none" if step is None else str(step)
        super().__init__(
            "Failed to encode resource: "
            f"source='{source_file}', resource='{resource}', "
            f"pattern='{pattern}', codec='{codec}', virtual={virtual}, "
            f"phase='{phase}', step={step_value}, method='{method}': {cause}"
        )
