"""TextExtractorUtility class for extracting text from various file types.

This module handles text extraction from PDFs, images (OCR), Office documents,
and various text-based file formats.
"""

import io
import json
import logging
import os
import re
import shutil
from typing import Any, Dict, Tuple

from PIL import Image, ImageOps, ImageFilter
from PyPDF2 import PdfReader
import pytesseract

from app.config import Config
from app.file_validator import get_file_type, _CODE_EXTENSIONS

logger = logging.getLogger(__name__)

# ==================== Optional Document Format Support ====================
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DocxDocument = None
    DOCX_AVAILABLE = False

try:
    from openpyxl import load_workbook
    XLSX_AVAILABLE = True
except ImportError:
    load_workbook = None
    XLSX_AVAILABLE = False

try:
    from pptx import Presentation
    PPTX_AVAILABLE = True
except ImportError:
    Presentation = None
    PPTX_AVAILABLE = False

try:
    from striprtf.striprtf import rtf_to_text
    RTF_AVAILABLE = True
except ImportError:
    rtf_to_text = None
    RTF_AVAILABLE = False

try:
    import ebooklib
    from ebooklib import epub
    from html.parser import HTMLParser
    EPUB_AVAILABLE = True
except ImportError:
    epub = None
    EPUB_AVAILABLE = False

try:
    from odf import text as odf_text, teletype
    from odf.opendocument import load as odf_load
    ODF_AVAILABLE = True
except ImportError:
    ODF_AVAILABLE = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False

# Groq Whisper uses direct HTTP API (no SDK needed — avoids pydantic conflicts)
GROQ_WHISPER_AVAILABLE = True

# Audio file extensions supported by Groq Whisper
AUDIO_EXTENSIONS = {'.mp3', '.mp4', '.m4a', '.wav', '.ogg', '.flac', '.webm', '.mpeg'}

# Extended image MIME types (WebP, BMP, TIFF, HEIC)
IMAGE_MIMETYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'image/bmp', 'image/tiff', 'image/heic', 'image/heif',
}

# ==================== OCR Configuration ====================
tesseract_cmd = getattr(Config, 'TESSERACT_CMD', None)
OCR_LANG_RAW = getattr(Config, 'TESSERACT_LANG', 'eng')


def normalize_langs(raw: str) -> Tuple[str, list]:
    if not raw:
        return 'eng', ['eng']
    # Allow comma, space, or plus separators
    parts = re.split(r'[,\s\+]+', raw.strip())
    parts = [p for p in parts if p]
    if not parts:
        parts = ['eng']
    # Deduplicate while preserving order
    seen = []
    for p in parts:
        lp = p.lower()
        if lp not in seen:
            seen.append(lp)
    return '+'.join(seen), seen


OCR_LANG, OCR_LANG_LIST = normalize_langs(OCR_LANG_RAW)

resolved_tesseract = None
if tesseract_cmd and shutil.which(tesseract_cmd):
    resolved_tesseract = shutil.which(tesseract_cmd)
else:
    fallback = shutil.which('tesseract')
    if fallback:
        resolved_tesseract = fallback

OCR_AVAILABLE = bool(resolved_tesseract)
if OCR_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = resolved_tesseract

# Optional EasyOCR fallback
try:
    import easyocr
    EASY_OCR_AVAILABLE = True
except Exception:
    easyocr = None
    EASY_OCR_AVAILABLE = False

easyocr_reader = None


def get_easyocr_reader():
    """Get or create EasyOCR reader instance."""
    global easyocr_reader
    if not EASY_OCR_AVAILABLE:
        return None
    if easyocr_reader is None:
        try:
            easyocr_reader = easyocr.Reader(OCR_LANG_LIST, gpu=False)
        except Exception as e:
            logger.warning(f"Failed to initialize EasyOCR reader: {e}")
            easyocr_reader = None
    return easyocr_reader


class TextExtractorUtility:
    """Utility class for extracting text from various file formats."""

    @staticmethod
    def extract_text(file_path: str) -> Tuple[str, Dict[str, Any]]:
        """Extract text from a file based on its type."""
        debug_info = {
            'file_type': None,
            'processing_steps': [],
            'error': None
        }

        try:
            with open(file_path, 'rb') as file:
                mime_type = get_file_type(file_path, file)
            debug_info['file_type'] = mime_type

            ext = os.path.splitext(file_path)[1].lower()

            # PDF
            if mime_type == 'application/pdf':
                return TextExtractorUtility._extract_from_pdf(file_path, debug_info)

            # Images — vision model first, Tesseract fallback
            elif mime_type in IMAGE_MIMETYPES or ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.heic', '.heif'):
                return TextExtractorUtility._extract_from_image(file_path, debug_info)

            # Plain text, markdown
            elif mime_type in ['text/plain', 'text/markdown']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # CSV
            elif mime_type == 'text/csv' or ext == '.csv':
                return TextExtractorUtility._extract_from_csv(file_path, debug_info)

            # JSON
            elif mime_type == 'application/json' or ext == '.json':
                return TextExtractorUtility._extract_from_json(file_path, debug_info)

            # RTF
            elif mime_type in ['application/rtf', 'text/rtf'] or ext == '.rtf':
                return TextExtractorUtility._extract_from_rtf(file_path, debug_info)

            # Audio files — Groq Whisper transcription
            elif ext in AUDIO_EXTENSIONS or mime_type.startswith('audio/'):
                return TextExtractorUtility._extract_from_audio(file_path, debug_info)

            # EPUB
            elif ext == '.epub' or mime_type == 'application/epub+zip':
                return TextExtractorUtility._extract_from_epub(file_path, debug_info)

            # ODF formats (LibreOffice)
            elif ext in ('.odt', '.ods', '.odp'):
                return TextExtractorUtility._extract_from_odf(file_path, debug_info)

            # Office Open XML formats (detected as ZIP or specific MIME)
            elif mime_type == 'application/zip' or mime_type.startswith('application/vnd.openxmlformats'):
                if ext == '.docx':
                    return TextExtractorUtility._extract_from_docx(file_path, debug_info)
                elif ext == '.xlsx':
                    return TextExtractorUtility._extract_from_xlsx(file_path, debug_info)
                elif ext == '.pptx':
                    return TextExtractorUtility._extract_from_pptx(file_path, debug_info)
                elif ext == '.epub':
                    return TextExtractorUtility._extract_from_epub(file_path, debug_info)
                else:
                    debug_info['error'] = f"Unsupported ZIP-based file type: {ext}"
                    return "", debug_info

            # Excel (specific MIME)
            elif mime_type == 'application/vnd.ms-excel' or ext in ['.xls', '.xlsx']:
                if ext == '.xlsx':
                    return TextExtractorUtility._extract_from_xlsx(file_path, debug_info)
                else:
                    debug_info['error'] = f"Old Excel format (.xls) not supported - please convert to .xlsx"
                    return "", debug_info

            # PowerPoint (specific MIME)
            elif mime_type == 'application/vnd.ms-powerpoint' or ext in ['.ppt', '.pptx']:
                if ext == '.pptx':
                    return TextExtractorUtility._extract_from_pptx(file_path, debug_info)
                else:
                    debug_info['error'] = f"Old PowerPoint format (.ppt) not supported - please convert to .pptx"
                    return "", debug_info

            # Word (specific MIME for old .doc format)
            elif mime_type == 'application/msword' or ext == '.doc':
                debug_info['error'] = f"Old Word format (.doc) not supported - please convert to .docx"
                return "", debug_info

            # XML/HTML/SVG - read as text
            elif mime_type in ['text/xml', 'application/xml', 'text/html', 'image/svg+xml'] or ext in ['.xml', '.html', '.htm', '.svg']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # YAML/TOML - read as text
            elif mime_type in ['application/x-yaml', 'text/yaml', 'application/toml', 'text/x-toml'] or ext in ['.yaml', '.yml', '.toml']:
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            # Code files - all text-based, check by extension or MIME
            elif (mime_type.startswith('text/') or
                  mime_type in ['application/javascript', 'application/x-php', 'application/x-sh', 'application/octet-stream'] or
                  ext in _CODE_EXTENSIONS):
                debug_info['processing_steps'].append(f'Processing as code/text file: {ext}')
                return TextExtractorUtility._extract_from_text(file_path, debug_info)

            else:
                debug_info['error'] = f"Unsupported mime type for text extraction: {mime_type}"
                return "", debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            logger.exception(f"Text extraction failed for {file_path}")
            return "", debug_info

    @staticmethod
    def _extract_from_pdf(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        try:
            reader = PdfReader(file_path)
            debug_info['processing_steps'].append('Opening PDF file')
            text = ""

            debug_info['processing_steps'].append(f'PDF pages found: {len(reader.pages)}')
            for i, page in enumerate(reader.pages):
                debug_info['processing_steps'].append(f'Processing page {i+1}')
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                except Exception as e:
                    debug_info['processing_steps'].append(f'Error on page {i+1}: {str(e)}')

            # Attempt OCR if no text extracted
            if not text.strip():
                debug_info['processing_steps'].append('No text extracted, attempting OCR')
                try:
                    for i, page in enumerate(reader.pages):
                        if page.images:
                            for image in page.images:
                                try:
                                    img_data = image.data
                                    img = Image.open(io.BytesIO(img_data))
                                    page_text = pytesseract.image_to_string(img)
                                    if page_text:
                                        text += page_text + "\n"
                                except Exception as img_e:
                                    debug_info['processing_steps'].append(f'OCR error on page {i+1}: {str(img_e)}')
                except Exception as e:
                    debug_info['processing_steps'].append(f'OCR processing error: {str(e)}')

            final_text = text.strip()
            if not final_text:
                debug_info['processing_steps'].append('No text could be extracted from the PDF')
            return final_text, debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"PDF extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_with_vision_model(file_path: str, debug_info: Dict[str, Any]) -> str:
        """Try vision model for image understanding (9/10 quality)."""
        import base64 as _b64

        # Read image as base64
        with open(file_path, 'rb') as f:
            img_bytes = f.read()
        b64_data = _b64.b64encode(img_bytes).decode()

        # Detect mime type
        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
                    '.tiff': 'image/tiff', '.tif': 'image/tiff', '.heic': 'image/heic'}
        mime = mime_map.get(ext, 'image/png')
        data_url = f"data:{mime};base64,{b64_data}"

        prompt = (
            "Extract ALL text visible in this image. Include headers, labels, "
            "numbers, captions, watermarks — everything readable. "
            "Preserve the layout structure. If it's a document, extract the full text. "
            "If it's a screenshot, describe the UI and extract all visible text. "
            "If there's no text, describe what you see in detail. "
            "Return only the extracted content, no commentary."
        )

        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]

        # Try Gemini first (500 RPD free)
        gemini_key = getattr(Config, 'GEMINI_API_KEY', None)
        if gemini_key:
            try:
                import requests as _req
                resp = _req.post(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    headers={"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"},
                    json={"model": "gemini-3.1-flash-lite-preview", "messages": messages, "max_tokens": 4096},
                    timeout=30,
                )
                if resp.status_code == 200:
                    text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                    if text and len(text.strip()) > 10:
                        debug_info['engine'] = 'gemini-vision'
                        debug_info['processing_steps'].append('Vision model: Gemini 3.1 Flash Lite')
                        return text.strip()
            except Exception as e:
                debug_info['processing_steps'].append(f'Gemini vision failed: {e}')

        # Try Groq Scout (free, unlimited)
        groq_key = getattr(Config, 'GROQ_API_KEY', None)
        if groq_key:
            try:
                import requests as _req
                resp = _req.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model": "meta-llama/llama-4-scout-17b-16e-instruct", "messages": messages, "max_tokens": 4096},
                    timeout=30,
                )
                if resp.status_code == 200:
                    text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                    if text and len(text.strip()) > 10:
                        debug_info['engine'] = 'groq-scout-vision'
                        debug_info['processing_steps'].append('Vision model: Groq Llama 4 Scout')
                        return text.strip()
            except Exception as e:
                debug_info['processing_steps'].append(f'Groq vision failed: {e}')

        return ""

    @staticmethod
    def _extract_with_tesseract(file_path: str, debug_info: Dict[str, Any]) -> str:
        """Tesseract OCR fallback (5/10 quality)."""
        if not OCR_AVAILABLE:
            return ""

        image = Image.open(file_path)
        ocr_config = '--oem 3 --psm 6'

        lang_candidates = []
        if OCR_LANG:
            lang_candidates.append(OCR_LANG)
        if 'eng' not in (OCR_LANG or ''):
            lang_candidates.append('eng')

        def try_tesseract(img, lang_label):
            try:
                txt = pytesseract.image_to_string(img, lang=lang_label, config=ocr_config)
                return (txt or '').strip()
            except pytesseract.TesseractError:
                return ""

        gray = ImageOps.grayscale(image)
        boosted = ImageOps.autocontrast(gray).filter(ImageFilter.SHARPEN)
        scale_factor = 1.5
        upscaled = boosted.resize(
            (int(boosted.width * scale_factor), int(boosted.height * scale_factor)),
            Image.LANCZOS
        )
        thresholded = upscaled.point(lambda p: 255 if p > 150 else 0)

        for lang_label in lang_candidates:
            for variant_name, variant_img in [
                ('native', image),
                ('preprocessed', boosted),
                ('upscale+threshold', thresholded),
            ]:
                debug_info['processing_steps'].append(f'Tesseract OCR ({variant_name}, lang={lang_label})')
                text = try_tesseract(variant_img, lang_label)
                if text:
                    debug_info['engine'] = 'tesseract'
                    debug_info['lang_used'] = lang_label
                    return text

        # EasyOCR fallback
        reader = get_easyocr_reader()
        if reader:
            try:
                results = reader.readtext(file_path, detail=0, paragraph=True)
                text = "\n".join(r for r in results if isinstance(r, str)).strip()
                if text:
                    debug_info['engine'] = 'easyocr'
                    return text
            except Exception:
                pass

        return ""

    @staticmethod
    def _extract_from_image(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from image: vision model first, Tesseract fallback."""
        try:
            debug_info['processing_steps'].append('Opening image file')

            # Try vision model first (9/10 quality)
            text = TextExtractorUtility._extract_with_vision_model(file_path, debug_info)
            if text:
                return text, debug_info

            # Fall back to Tesseract (5/10 quality)
            debug_info['processing_steps'].append('Vision models unavailable, falling back to Tesseract')
            text = TextExtractorUtility._extract_with_tesseract(file_path, debug_info)
            if text:
                return text, debug_info

            debug_info['error'] = 'No text could be extracted from image'
            return "", debug_info

        except Exception as e:
            debug_info['error'] = str(e)
            return "", debug_info

    @staticmethod
    def _extract_from_text(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        try:
            debug_info['processing_steps'].append('Reading text file')
            with open(file_path, 'r', encoding='utf-8') as file:
                text = file.read()
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            return "", debug_info

    @staticmethod
    def _extract_from_docx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from DOCX files using python-docx."""
        if not DOCX_AVAILABLE:
            debug_info['error'] = 'python-docx not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening DOCX file')
            doc = DocxDocument(file_path)

            paragraphs = []
            for para in doc.paragraphs:
                if para.text.strip():
                    paragraphs.append(para.text)

            # Also extract text from tables
            for table in doc.tables:
                for row in table.rows:
                    row_text = []
                    for cell in row.cells:
                        if cell.text.strip():
                            row_text.append(cell.text.strip())
                    if row_text:
                        paragraphs.append(' | '.join(row_text))

            text = '\n'.join(paragraphs)
            debug_info['processing_steps'].append(f'Extracted {len(paragraphs)} paragraphs')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"DOCX extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_xlsx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from Excel files using openpyxl."""
        if not XLSX_AVAILABLE:
            debug_info['error'] = 'openpyxl not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening Excel file')
            wb = load_workbook(file_path, read_only=True, data_only=True)

            all_text = []
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                all_text.append(f"=== Sheet: {sheet_name} ===")

                for row in sheet.iter_rows(values_only=True):
                    row_values = [str(cell) if cell is not None else '' for cell in row]
                    # Skip empty rows
                    if any(v.strip() for v in row_values):
                        all_text.append(' | '.join(row_values))

            wb.close()
            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(wb.sheetnames)} sheets')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"Excel extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_pptx(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from PowerPoint files using python-pptx."""
        if not PPTX_AVAILABLE:
            debug_info['error'] = 'python-pptx not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Opening PowerPoint file')
            prs = Presentation(file_path)

            all_text = []
            for slide_num, slide in enumerate(prs.slides, 1):
                slide_text = [f"=== Slide {slide_num} ==="]
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_text.append(shape.text)
                    # Extract text from tables
                    if shape.has_table:
                        for row in shape.table.rows:
                            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                            if row_text:
                                slide_text.append(' | '.join(row_text))
                if len(slide_text) > 1:  # More than just the header
                    all_text.extend(slide_text)

            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(prs.slides)} slides')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"PowerPoint extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_csv(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from CSV files."""
        try:
            debug_info['processing_steps'].append('Reading CSV file')
            import csv

            all_text = []
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                for row in reader:
                    if any(cell.strip() for cell in row):
                        all_text.append(' | '.join(row))

            text = '\n'.join(all_text)
            debug_info['processing_steps'].append(f'Extracted {len(all_text)} rows')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"CSV extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_json(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from JSON files."""
        try:
            debug_info['processing_steps'].append('Reading JSON file')

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Pretty print the JSON for readability
            text = json.dumps(data, indent=2, ensure_ascii=False)
            debug_info['processing_steps'].append('Parsed JSON successfully')
            return text, debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"JSON extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_rtf(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from RTF files using striprtf."""
        if not RTF_AVAILABLE:
            debug_info['error'] = 'striprtf not installed'
            return "", debug_info
        try:
            debug_info['processing_steps'].append('Reading RTF file')

            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                rtf_content = f.read()

            text = rtf_to_text(rtf_content)
            debug_info['processing_steps'].append('Extracted text from RTF')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"RTF extraction error: {str(e)}")
            return "", debug_info


    @staticmethod
    def _extract_from_audio(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Transcribe audio using Groq Whisper API (free, fast)."""
        import requests as _req

        groq_key = getattr(Config, 'GROQ_API_KEY', None)
        if not groq_key:
            debug_info['error'] = 'GROQ_API_KEY not configured for audio transcription'
            return "", debug_info

        try:
            debug_info['processing_steps'].append('Transcribing audio with Groq Whisper')
            with open(file_path, "rb") as f:
                resp = _req.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    files={"file": (os.path.basename(file_path), f)},
                    data={"model": "whisper-large-v3-turbo", "response_format": "text"},
                    timeout=120,
                )
            if resp.status_code != 200:
                debug_info['error'] = f'Whisper API error: {resp.status_code} {resp.text[:200]}'
                return "", debug_info

            text = resp.text.strip()
            if text:
                debug_info['engine'] = 'groq-whisper'
                debug_info['processing_steps'].append(f'Transcribed {len(text)} characters')
                return f"[Audio Transcription]\n{text}", debug_info
            else:
                debug_info['error'] = 'Whisper returned empty transcription'
                return "", debug_info
        except Exception as e:
            debug_info['error'] = f'Audio transcription failed: {str(e)}'
            logger.error(f"Audio transcription error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_epub(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from EPUB ebooks."""
        if not EPUB_AVAILABLE:
            debug_info['error'] = 'ebooklib not installed'
            return "", debug_info

        try:
            debug_info['processing_steps'].append('Opening EPUB file')

            class _HTMLStripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data)
                def get_text(self):
                    return ''.join(self.text)

            book = epub.read_epub(file_path, options={'ignore_ncx': True})
            chapters = []
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                content = item.get_content().decode('utf-8', errors='replace')
                stripper = _HTMLStripper()
                stripper.feed(content)
                chapter_text = stripper.get_text().strip()
                if chapter_text:
                    chapters.append(chapter_text)

            text = '\n\n'.join(chapters)
            debug_info['processing_steps'].append(f'Extracted {len(chapters)} chapters')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"EPUB extraction error: {str(e)}")
            return "", debug_info

    @staticmethod
    def _extract_from_odf(file_path: str, debug_info: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract text from ODF files (ODT, ODS, ODP)."""
        if not ODF_AVAILABLE:
            debug_info['error'] = 'odfpy not installed'
            return "", debug_info

        try:
            debug_info['processing_steps'].append('Opening ODF file')
            doc = odf_load(file_path)
            paragraphs = doc.getElementsByType(odf_text.P)
            text_parts = []
            for para in paragraphs:
                t = teletype.extractText(para)
                if t and t.strip():
                    text_parts.append(t.strip())

            text = '\n'.join(text_parts)
            debug_info['processing_steps'].append(f'Extracted {len(text_parts)} paragraphs from ODF')
            return text.strip(), debug_info
        except Exception as e:
            debug_info['error'] = str(e)
            logger.error(f"ODF extraction error: {str(e)}")
            return "", debug_info


def extract_text_concurrently(file_path: str) -> Tuple[str, Dict[str, Any]]:
    """Wrapper function for extracting text from a file."""
    text, debug_info = TextExtractorUtility.extract_text(file_path)
    if debug_info.get('error'):
        logger.error(f"Text extraction error: {debug_info['error']}")
    return text, debug_info
