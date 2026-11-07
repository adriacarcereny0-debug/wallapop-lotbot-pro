"""Generación automática de imágenes de producto (una por anuncio)."""

from lot_bot.images.generation.base import GenerationError, GenerationTimeout, ImageGenerator
from lot_bot.images.generation.demo import DemoImageGenerator
from lot_bot.images.generation.flux import FluxImageService
from lot_bot.images.generation.prompts import ProductImageSpec, build_prompt, spec_from_master
from lot_bot.images.generation.registry import GeneratedImageResult, ImageGenerationService

__all__ = [
    "GenerationError",
    "GenerationTimeout",
    "ImageGenerator",
    "DemoImageGenerator",
    "FluxImageService",
    "ProductImageSpec",
    "build_prompt",
    "spec_from_master",
    "GeneratedImageResult",
    "ImageGenerationService",
]
