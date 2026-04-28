"""Image generation helpers.

Supports OpenRouter (primary) with Pollinations fallback.
All functions are pure (no Flask session/request dependencies).
"""

import base64
import logging
import mimetypes
import os
import re
import uuid
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import requests
from PIL import Image

from .config import Config

logger = logging.getLogger(__name__)


ASPECT_SIZES: Dict[str, Tuple[int, int]] = {
    "square": (1024, 1024),
    "portrait": (1024, 1536),
    "landscape": (1536, 1024),
}

_PORTRAIT_RE = re.compile(
    r"\b(portrait|selfie|full[- ]?body|headshot|mugshot|tall|vertical|"
    r"standing|person standing|phone wallpaper|story|reel|tiktok|9:16|2:3|3:4)\b",
    re.IGNORECASE,
)
_LANDSCAPE_RE = re.compile(
    r"\b(landscape|panorama|panoramic|wide[- ]?shot|banner|cinematic|"
    r"cityscape|skyline|horizon|wallpaper|desktop wallpaper|scenery|"
    r"vista|16:9|21:9|3:2|4:3)\b",
    re.IGNORECASE,
)
_SQUARE_RE = re.compile(
    r"\b(square|icon|avatar|profile picture|album cover|logo|sticker|"
    r"emoji|thumbnail|1:1)\b",
    re.IGNORECASE,
)


def _classify_aspect_by_keywords(prompt: str) -> Optional[str]:
    """Cheap keyword pass. Returns 'square'|'portrait'|'landscape' or None if ambiguous."""
    if not prompt:
        return None
    hits = {
        "square": bool(_SQUARE_RE.search(prompt)),
        "portrait": bool(_PORTRAIT_RE.search(prompt)),
        "landscape": bool(_LANDSCAPE_RE.search(prompt)),
    }
    matched = [name for name, hit in hits.items() if hit]
    if len(matched) == 1:
        return matched[0]
    return None


@lru_cache(maxsize=512)
def _classify_aspect_with_llm(prompt: str) -> str:
    """Ask a small metadata model to pick an aspect. Returns 'square' on any failure."""
    try:
        from app.utilities import send_prompt
        classifier_prompt = (
            "Pick the best image aspect ratio for this prompt. "
            "Reply with exactly one lowercase word: square, portrait, or landscape. "
            "Rules: people/characters -> portrait; landscapes/scenery/cities/wide scenes -> landscape; "
            "single objects/food/animals/abstract -> square.\n\n"
            f"Prompt: {prompt}\n\nAnswer:"
        )
        model = getattr(Config, 'METADATA_MODEL_PRIMARY', 'gemini-2.5-flash')
        response, _ = send_prompt(classifier_prompt, model=model)
        if not response:
            return "square"
        word = response.strip().lower().split()[0] if response.strip() else ""
        word = re.sub(r'[^a-z]', '', word)
        if word in ASPECT_SIZES:
            return word
        return "square"
    except Exception as exc:
        logger.debug(f"Aspect LLM classifier failed, defaulting to square: {exc}")
        return "square"


def resolve_aspect(prompt: str, override: Optional[str] = None) -> Tuple[str, int, int]:
    """Resolve (aspect_name, width, height) for a prompt.

    override can be 'square'|'portrait'|'landscape' to skip inference.
    Keyword pass runs first; ambiguous prompts fall through to the LLM classifier.
    """
    aspect: Optional[str] = None
    if override and override.lower() in ASPECT_SIZES:
        aspect = override.lower()
    if aspect is None:
        aspect = _classify_aspect_by_keywords(prompt)
    if aspect is None:
        aspect = _classify_aspect_with_llm(prompt)
    width, height = ASPECT_SIZES[aspect]
    return aspect, width, height


def _generated_image_dir(user_id: str) -> str:
    sanitized_user_id = re.sub(r'[^\w\-]', '_', user_id or '')
    output_dir = os.path.join(Config.UPLOAD_FOLDER, sanitized_user_id, "generated_images")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def save_image_bytes_to_user_dir(
    image_bytes: bytes,
    user_id: str,
    prefix: str,
    seed: Optional[int] = None,
    extension: str = "png"
) -> Optional[str]:
    try:
        output_dir = _generated_image_dir(user_id)
        identifier = seed if seed is not None else uuid.uuid4().hex
        filename = f"{prefix}_{identifier}.{extension}"
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "wb") as handle:
            handle.write(image_bytes)
        with Image.open(file_path) as img:
            img.verify()
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to persist generated image bytes: {e}")
        try:
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        return None


def save_remote_image(image_url: str, user_id: str, prefix: str, seed: Optional[int] = None) -> Optional[str]:
    if not image_url:
        return None
    response = None
    file_path = None
    try:
        response = requests.get(image_url, timeout=30, stream=True)
        response.raise_for_status()
        output_dir = _generated_image_dir(user_id)
        identifier = seed if seed is not None else uuid.uuid4().hex
        ext = "png"
        content_type = response.headers.get('Content-Type', '')
        if "jpeg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"
        filename = f"{prefix}_{identifier}.{ext}"
        file_path = os.path.join(output_dir, filename)
        max_bytes = 10 * 1024 * 1024
        total = 0
        with open(file_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    os.remove(file_path)
                    logger.warning("Remote image exceeded size cap; download aborted.")
                    return None
                handle.write(chunk)
        with Image.open(file_path) as img:
            img.verify()
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to download remote image {image_url}: {e}")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return None


def _save_data_url_image(data_url: str, user_id: str, prefix: str, seed: Optional[int] = None) -> Optional[str]:
    if not data_url.startswith("data:"):
        return None
    try:
        header, encoded = data_url.split(",", 1)
    except ValueError:
        return None
    mime_match = re.match(r'data:(.*?);base64', header)
    mime_type = mime_match.group(1) if mime_match else "image/png"
    try:
        image_bytes = base64.b64decode(encoded)
    except Exception as e:
        logger.warning(f"Failed to decode image data URL: {e}")
        return None
    extension = mimetypes.guess_extension(mime_type) or ".png"
    extension = extension.lstrip(".")
    return save_image_bytes_to_user_dir(image_bytes, user_id, prefix, seed, extension=extension)


def pollinations_url(prompt: str, seed: int, width: int = 1024, height: int = 1024) -> str:
    suffix = "without watermark"
    full_prompt = f"{prompt}, {suffix}" if prompt else suffix
    encoded_prompt = requests.utils.quote(full_prompt)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width={width}&height={height}"


def save_pollinations_image(prompt: str, seed: int, user_id: str, width: int = 1024, height: int = 1024) -> Optional[str]:
    """Download and save a Pollinations image to the uploads folder; return relative path on success."""
    url = pollinations_url(prompt, seed, width, height)
    try:
        output_dir = _generated_image_dir(user_id)
        filename = f"pollinations_{seed}.png"
        file_path = os.path.join(output_dir, filename)
        if os.path.exists(file_path):
            return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
        response = requests.get(url, timeout=30, stream=True)
        response.raise_for_status()
        max_bytes = 10 * 1024 * 1024  # 10 MB cap
        total = 0
        with open(file_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    os.remove(file_path)
                    logger.warning("Pollinations image exceeded size cap; aborted download.")
                    return None
                handle.write(chunk)
        try:
            with Image.open(file_path) as img:
                img.verify()
        except Exception:
            os.remove(file_path)
            logger.warning("Pollinations download failed image verification; file removed.")
            return None
        return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
    except Exception as e:
        logger.warning(f"Failed to save Pollinations image locally: {e}")
        return None


def _infer_aspect_ratio(size_value: str) -> Optional[str]:
    if not size_value:
        return None
    size_value = size_value.strip().lower()
    if ":" in size_value:
        return size_value
    mapping = {
        "1024x1024": "1:1",
        "832x1248": "2:3",
        "1248x832": "3:2",
        "864x1184": "3:4",
        "1184x864": "4:3",
        "896x1152": "4:5",
        "1152x896": "5:4",
        "768x1344": "9:16",
        "1344x768": "16:9",
        "1536x672": "21:9",
    }
    return mapping.get(size_value)


def generate_image_with_openrouter(prompt: str, user_id: str, seed: int, size: Optional[str] = None) -> Optional[Dict[str, Any]]:
    api_key = getattr(Config, 'OPENROUTER_API_KEY', None)
    if not api_key:
        logger.error("OpenRouter API key is not configured.")
        return None
    url = getattr(Config, 'OPENROUTER_IMAGE_URL', 'https://openrouter.ai/api/v1/chat/completions')
    model = getattr(Config, 'OPENROUTER_IMAGE_MODEL', 'google/gemini-2.5-flash-image')
    model_label = getattr(Config, 'OPENROUTER_IMAGE_MODEL_LABEL', model)
    if not size:
        size = getattr(Config, 'OPENROUTER_IMAGE_SIZE', '1024x1024')

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if getattr(Config, 'OPENROUTER_SITE_URL', None):
        headers["HTTP-Referer"] = Config.OPENROUTER_SITE_URL
    if getattr(Config, 'OPENROUTER_APP_NAME', None):
        headers["X-Title"] = Config.OPENROUTER_APP_NAME

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "modalities": ["image", "text"]
    }
    aspect_ratio = _infer_aspect_ratio(size)
    if aspect_ratio:
        payload["image_config"] = {"aspect_ratio": aspect_ratio}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            logger.error(f"OpenRouter image generation returned invalid JSON: {response.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"OpenRouter image generation failed: {e}")
        return None

    choices = data.get('choices') if isinstance(data, dict) else None
    if not choices:
        logger.warning("OpenRouter image API returned no choices.")
        return None

    message = choices[0].get("message", {})
    images = message.get("images") or []
    image_url = None
    local_path = None

    for entry in images:
        candidate = entry.get("image_url", {}).get("url")
        if not candidate:
            continue
        image_url = candidate
        if candidate.startswith("data:"):
            local_path = _save_data_url_image(candidate, user_id, prefix="openrouter", seed=seed)
        else:
            local_path = save_remote_image(candidate, user_id, prefix="openrouter", seed=seed)
        if local_path:
            break

    if not image_url:
        logger.warning("OpenRouter image payload missing usable URL.")
        return None

    return {
        'image_url': image_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'openrouter',
        'model': model,
        'model_label': model_label
    }


def generate_image_with_openai(prompt: str, user_id: str, seed: int, size: str = "1024x1024") -> Optional[Dict[str, Any]]:
    """Generate image using OpenAI gpt-image-1 API."""
    api_key = getattr(Config, 'OPENAI_API_KEY', None)
    if not api_key:
        logger.warning("OpenAI API key not configured for image generation.")
        return None

    try:
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": 1,
                "size": size,
                "quality": "low",
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"OpenAI image generation failed: {e}")
        return None

    items = data.get("data", [])
    if not items:
        logger.warning("OpenAI image API returned no data.")
        return None

    item = items[0]
    b64_json = item.get("b64_json", "")
    image_url = item.get("url", "")
    local_path = None

    if b64_json:
        try:
            image_bytes = base64.b64decode(b64_json)
            local_path = save_image_bytes_to_user_dir(image_bytes, user_id, "openai_img", seed)
        except Exception as e:
            logger.warning(f"Failed to decode OpenAI image: {e}")
    elif image_url:
        local_path = save_remote_image(image_url, user_id, "openai_img", seed)

    if not local_path and not image_url:
        return None

    # Build a data URL for frontend display if we have base64
    display_url = image_url
    if not display_url and b64_json:
        display_url = f"data:image/png;base64,{b64_json}"

    return {
        'image_url': display_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'openai',
        'model': 'gpt-image-1',
        'model_label': 'GPT Image 1',
    }


def generate_pollinations_image(prompt: str, user_id: str, seed: int, width: int = 1024, height: int = 1024) -> Optional[Dict[str, Any]]:
    image_url = pollinations_url(prompt, seed, width, height)
    local_path = save_pollinations_image(prompt, seed, user_id, width, height)
    return {
        'image_url': image_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'pollinations',
        'model': 'pollinations',
        'model_label': 'Pollinations'
    }


def _record_image_usage(result: Optional[Dict[str, Any]]) -> None:
    """Decrement the provider's quota for a successful image generation.

    Uses the orchestrator accountant so the same per-provider bucket that
    tracks chat / search usage also counts images. Without this, a user
    could exhaust every provider's image quota without ever affecting the
    orchestrator's routing decisions.
    """
    if not result or not isinstance(result, dict):
        return
    model_id = result.get('model')
    if not model_id:
        return
    try:
        from app.orchestrator import record_usage
        # Image API calls don't expose token counts — charge a nominal
        # 1-output-token to count the request in daily quotas. Individual
        # providers' cost/1M numbers handle the actual $ bookkeeping.
        record_usage(model_id, input_tokens=0, output_tokens=1)
    except Exception as exc:  # pragma: no cover — non-fatal
        logger.debug(f"record_usage skipped for image model {model_id}: {exc}")


def generate_image_result(user_id: str, prompt: str, aspect_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    seed = abs(hash(f"{user_id}:{prompt}")) % (2**31)
    aspect, width, height = resolve_aspect(prompt, override=aspect_override)
    size_str = f"{width}x{height}"
    logger.info(f"Image aspect resolved to {aspect} ({size_str}) for prompt (len={len(prompt)})")
    # Chain: OpenRouter → Pollinations (free) → OpenAI gpt-image-1 (paid, last resort)
    result = generate_image_with_openrouter(prompt, user_id, seed, size=size_str)
    if result:
        result['aspect'] = aspect
        _record_image_usage(result)
        return result
    logger.info("OpenRouter failed, trying Pollinations.")
    result = generate_pollinations_image(prompt, user_id, seed, width=width, height=height)
    if result and result.get('local_path'):
        result['aspect'] = aspect
        _record_image_usage(result)
        return result
    logger.info("Pollinations failed, falling back to OpenAI gpt-image-1.")
    final = generate_image_with_openai(prompt, user_id, seed, size=size_str) or result
    if final:
        final['aspect'] = aspect
    _record_image_usage(final)
    return final
