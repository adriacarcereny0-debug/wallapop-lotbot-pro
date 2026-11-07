"""Catalogo de productos, control de calidad y duplicados."""

from lot_bot.catalog.duplicates import (
    DuplicateGroup,
    MatchReason,
    find_duplicate_images,
    find_duplicates,
    find_similar,
    title_similarity,
)
from lot_bot.catalog.service import CatalogService, ProductFilter
from lot_bot.catalog.validation import (
    ALLOWED_IMAGE_FORMATS,
    Issue,
    QualityReport,
    Severity,
    validate_listing_data,
)

__all__ = [
    "CatalogService",
    "ProductFilter",
    "DuplicateGroup",
    "MatchReason",
    "find_duplicates",
    "find_duplicate_images",
    "find_similar",
    "title_similarity",
    "QualityReport",
    "Issue",
    "Severity",
    "validate_listing_data",
    "ALLOWED_IMAGE_FORMATS",
]
