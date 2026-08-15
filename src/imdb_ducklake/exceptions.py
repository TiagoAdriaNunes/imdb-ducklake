"""Application-specific exception hierarchy."""


class ImdbLakehouseError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(ImdbLakehouseError):
    """Raised when application configuration is invalid."""


class AcquisitionError(ImdbLakehouseError):
    """Raised when a source artifact cannot be acquired or validated."""


class IngestionError(ImdbLakehouseError):
    """Raised when dlt cannot load source artifacts into DuckLake."""


class TransformationError(ImdbLakehouseError):
    """Raised when dbt cannot build or validate transformations."""


class ValidationError(ImdbLakehouseError):
    """Raised when the resulting lakehouse fails an acceptance check."""


class PromotionError(ImdbLakehouseError):
    """Raised when a validated build cannot become the current build."""
