"""Engine-level exceptions."""


class EngineError(Exception):
    """Base class for all AI engine errors."""


class ModelUnavailableError(EngineError):
    """A required model runtime or weights file is not installed/configured."""


class UnsupportedImageError(EngineError):
    """The DICOM object has no pixel data the engine can interpret."""
