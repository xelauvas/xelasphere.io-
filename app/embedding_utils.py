"""
Embedding Utilities - Quantization & Storage Optimization
==========================================================

Reduces Firestore storage costs by quantizing embeddings:
- float32 (384 dims) = 1,536 bytes per embedding
- float16 (384 dims) = 768 bytes per embedding
- int8 quantized     = 384 bytes per embedding (4x smaller!)

Also provides base64 encoding for efficient Firestore storage.
"""

import base64
import struct
import logging
from typing import List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# Embedding dimension for all-MiniLM-L6-v2
EMBEDDING_DIM = 384

# Quantization constants
QUANTIZE_SCALE = 127.0  # Scale for int8 quantization


def quantize_embedding(embedding: List[float]) -> Tuple[bytes, float, float]:
    """
    Quantize float32 embedding to int8 bytes.

    Uses min-max normalization to map floats to int8 range [-127, 127].
    Stores scale factors for dequantization.

    Args:
        embedding: List of float32 values (typically 384 dims)

    Returns:
        Tuple of (quantized_bytes, min_val, max_val)
    """
    if not embedding:
        return b'', 0.0, 0.0

    arr = np.array(embedding, dtype=np.float32)

    # Get min/max for denormalization
    min_val = float(arr.min())
    max_val = float(arr.max())

    # Avoid division by zero
    val_range = max_val - min_val
    if val_range < 1e-9:
        # All values are the same
        quantized = np.zeros(len(embedding), dtype=np.int8)
    else:
        # Normalize to [0, 1] then scale to [-127, 127]
        normalized = (arr - min_val) / val_range
        quantized = ((normalized * 254) - 127).astype(np.int8)

    return quantized.tobytes(), min_val, max_val


def dequantize_embedding(data: bytes, min_val: float, max_val: float) -> List[float]:
    """
    Dequantize int8 bytes back to float32 embedding.

    Args:
        data: Quantized int8 bytes
        min_val: Original minimum value
        max_val: Original maximum value

    Returns:
        List of float32 values
    """
    if not data:
        return []

    quantized = np.frombuffer(data, dtype=np.int8)

    val_range = max_val - min_val
    if val_range < 1e-9:
        return [min_val] * len(quantized)

    # Reverse the quantization
    normalized = (quantized.astype(np.float32) + 127) / 254
    arr = normalized * val_range + min_val

    return arr.tolist()


def embedding_to_storage(embedding: List[float]) -> dict:
    """
    Convert embedding to compact storage format for Firestore.

    Instead of storing 384 floats (expensive!), stores:
    - Base64-encoded int8 bytes (512 chars vs 3000+ chars for float array)
    - Min/max for dequantization

    Args:
        embedding: List of float32 values

    Returns:
        Dict with 'data', 'min', 'max' keys
    """
    if not embedding:
        return {'data': '', 'min': 0.0, 'max': 0.0}

    quantized, min_val, max_val = quantize_embedding(embedding)

    return {
        'data': base64.b64encode(quantized).decode('ascii'),
        'min': round(min_val, 6),
        'max': round(max_val, 6)
    }


def embedding_from_storage(storage: dict) -> List[float]:
    """
    Restore embedding from compact storage format.

    Args:
        storage: Dict with 'data', 'min', 'max' keys

    Returns:
        List of float32 values
    """
    if not storage or not storage.get('data'):
        return []

    try:
        data = base64.b64decode(storage['data'])
        min_val = storage.get('min', 0.0)
        max_val = storage.get('max', 0.0)
        return dequantize_embedding(data, min_val, max_val)
    except Exception as e:
        logger.warning(f"Failed to decode embedding: {e}")
        return []


def is_quantized_format(embedding_data) -> bool:
    """
    Check if embedding data is in quantized format or raw float list.

    Args:
        embedding_data: Either a dict (quantized) or list (raw floats)

    Returns:
        True if quantized format, False if raw
    """
    if isinstance(embedding_data, dict) and 'data' in embedding_data:
        return True
    return False


def normalize_embedding(embedding_data) -> List[float]:
    """
    Normalize embedding data to float list, handling both formats.

    Args:
        embedding_data: Either quantized dict or raw float list

    Returns:
        List of float32 values
    """
    if not embedding_data:
        return []

    if is_quantized_format(embedding_data):
        return embedding_from_storage(embedding_data)

    if isinstance(embedding_data, list):
        return embedding_data

    return []


# =============================================================================
# Float16 Alternative (simpler but less compression)
# =============================================================================

def embedding_to_float16(embedding: List[float]) -> str:
    """
    Convert embedding to float16 base64 string.

    Simpler than int8 quantization but only 2x compression (vs 4x).

    Args:
        embedding: List of float32 values

    Returns:
        Base64-encoded float16 bytes
    """
    if not embedding:
        return ''

    arr = np.array(embedding, dtype=np.float16)
    return base64.b64encode(arr.tobytes()).decode('ascii')


def embedding_from_float16(data: str) -> List[float]:
    """
    Restore embedding from float16 base64 string.

    Args:
        data: Base64-encoded float16 bytes

    Returns:
        List of float32 values
    """
    if not data:
        return []

    try:
        raw = base64.b64decode(data)
        arr = np.frombuffer(raw, dtype=np.float16)
        return arr.astype(np.float32).tolist()
    except Exception as e:
        logger.warning(f"Failed to decode float16 embedding: {e}")
        return []


# =============================================================================
# Storage Size Comparison
# =============================================================================

def compare_storage_sizes(embedding: List[float]) -> dict:
    """
    Compare storage sizes of different formats.

    Args:
        embedding: Original float32 embedding

    Returns:
        Dict with size comparisons
    """
    if not embedding:
        return {}

    import json

    # Raw JSON array
    raw_json = json.dumps(embedding)

    # Quantized format
    quantized = embedding_to_storage(embedding)
    quantized_json = json.dumps(quantized)

    # Float16 format
    f16 = embedding_to_float16(embedding)
    f16_json = json.dumps({'data': f16})

    return {
        'raw_json_bytes': len(raw_json),
        'quantized_json_bytes': len(quantized_json),
        'float16_json_bytes': len(f16_json),
        'quantized_ratio': len(quantized_json) / len(raw_json),
        'float16_ratio': len(f16_json) / len(raw_json),
    }
