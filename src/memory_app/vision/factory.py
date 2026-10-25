from memory_app.llms.compatible import CompatibleVisionClient
from memory_app.vision.caption import CaptionAnalyzer
from memory_app.vision.ocr import TesseractAnalyzer
from memory_app.vision.pipeline import ImagePipeline


def build_vision(settings):
    settings.validate()
    if settings.vision not in {"none", "compatible"}:
        raise ValueError("MEMORY_VISION must be none or compatible")
    if settings.ocr not in {"none", "tesseract"}:
        raise ValueError("MEMORY_OCR must be none or tesseract")
    analyzers = []
    if settings.vision == "compatible":
        analyzers.append(CaptionAnalyzer(CompatibleVisionClient(
            settings.vision_base_url, settings.vision_api_key, settings.vision_model,
            settings.vision_response_format),
            settings.vision_revision))
    if settings.ocr == "tesseract":
        analyzers.append(TesseractAnalyzer(settings.tesseract_executable,
                                            settings.ocr_languages, settings.ocr_revision))
    return ImagePipeline(analyzers) if analyzers else None
