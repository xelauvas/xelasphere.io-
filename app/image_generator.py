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
from typing import Any, Dict, Optional

import requests
from PIL import Image

from .config import Config

logger = logging.getLogger(__name__)


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


def pollinations_url(prompt: str, seed: int) -> str:
    suffix = "without watermark"
    full_prompt = f"{prompt}, {suffix}" if prompt else suffix
    encoded_prompt = requests.utils.quote(full_prompt)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width=1024&height=1024"


def save_pollinations_image(prompt: str, seed: int, user_id: str) -> Optional[str]:
    """Download and save a Pollinations image to the uploads folder; return relative path on success."""
    url = pollinations_url(prompt, seed)
    try:
        output_dir = _generated_image_dir(user_id)
        filename = f"pollinations_{seed}.png"
        file_path = os.path.join(output_dir, filename)
        if os.path.exists(file_path):
            return os.path.relpath(file_path, Config.UPLOAD_FOLDER)
        response = requests.get(url, timeout=20, stream=True)
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


def generate_image_with_openrouter(prompt: str, user_id: str, seed: int) -> Optional[Dict[str, Any]]:
    api_key = getattr(Config, 'OPENROUTER_API_KEY', None)
    if not api_key:
        logger.error("OpenRouter API key is not configured.")
        return None
    url = getattr(Config, 'OPENROUTER_IMAGE_URL', 'https://openrouter.ai/api/v1/chat/completions')
    model = getattr(Config, 'OPENROUTER_IMAGE_MODEL', 'google/gemini-2.5-flash-image')
    model_label = getattr(Config, 'OPENROUTER_IMAGE_MODEL_LABEL', model)
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
        response = requests.post(url, json=payload, headers=headers, timeout=60)
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


def generate_pollinations_image(prompt: str, user_id: str, seed: int) -> Optional[Dict[str, Any]]:
    image_url = pollinations_url(prompt, seed)
    local_path = save_pollinations_image(prompt, seed, user_id)
    return {
        'image_url': image_url,
        'local_path': local_path,
        'seed': seed,
        'prompt': prompt,
        'provider': 'pollinations',
        'model': 'pollinations',
        'model_label': 'Pollinations'
    }


def generate_image_result(user_id: str, prompt: str) -> Optional[Dict[str, Any]]:
    seed = abs(hash(f"{user_id}:{prompt}")) % (2**31)
    result = generate_image_with_openrouter(prompt, user_id, seed)
    if result:
        return result
    logger.info("Falling back to Pollinations for image generation.")
    return generate_pollinations_image(prompt, user_id, seed)
