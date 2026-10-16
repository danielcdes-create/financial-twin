from app.services.ai.xai.provider import XAIVisionProvider, XAITextProvider

class ImageGenerationProvider:
    """Intentionally separate capability boundary; image generation is not used by the MVP."""
    def generate(self, prompt: str):
        raise NotImplementedError("Image generation is not part of the MVP")
