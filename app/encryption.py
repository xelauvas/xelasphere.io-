# encryption.py
"""
Utility module for encrypting/decrypting sensitive data like user API keys.
Uses Fernet symmetric encryption from the cryptography library.
"""

import os
import base64
import logging
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Cache the Fernet instance
_fernet_instance = None


def _get_fernet():
    """
    Get or create a Fernet instance using the API_ENCRYPTION_KEY from environment.
    Falls back to deriving a key from FLASK_SECRET_KEY if API_ENCRYPTION_KEY is not set.
    """
    global _fernet_instance

    if _fernet_instance is not None:
        return _fernet_instance

    encryption_key = os.getenv('API_ENCRYPTION_KEY')

    if encryption_key:
        # Use the provided Fernet key directly
        try:
            _fernet_instance = Fernet(encryption_key.encode())
            return _fernet_instance
        except Exception as e:
            logger.error(f"Invalid API_ENCRYPTION_KEY format: {e}")
            raise ValueError("API_ENCRYPTION_KEY must be a valid Fernet key")

    # Fall back to deriving from FLASK_SECRET_KEY
    secret_key = os.getenv('FLASK_SECRET_KEY')
    if not secret_key:
        raise ValueError("Neither API_ENCRYPTION_KEY nor FLASK_SECRET_KEY is set")

    # Derive a Fernet-compatible key from the secret key using PBKDF2
    salt = b'xelasphere_api_keys_v1'  # Static salt for consistency
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
    _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_api_key(plaintext: str) -> str:
    """
    Encrypt an API key for secure storage.

    Args:
        plaintext: The API key to encrypt

    Returns:
        Base64-encoded encrypted string
    """
    if not plaintext:
        return ''

    try:
        fernet = _get_fernet()
        encrypted = fernet.encrypt(plaintext.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Failed to encrypt API key: {e}")
        raise


def decrypt_api_key(ciphertext: str) -> str:
    """
    Decrypt an API key from storage.

    Args:
        ciphertext: The encrypted API key

    Returns:
        Decrypted plaintext API key
    """
    if not ciphertext:
        return ''

    try:
        fernet = _get_fernet()
        decrypted = fernet.decrypt(ciphertext.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.error("Failed to decrypt API key: invalid token (key may have changed)")
        raise ValueError("Unable to decrypt API key - encryption key may have changed")
    except Exception as e:
        logger.error(f"Failed to decrypt API key: {e}")
        raise


def is_encrypted(value: str) -> bool:
    """
    Check if a value appears to be Fernet-encrypted.
    Fernet tokens start with 'gAAAAA' (base64 of version byte + timestamp).
    """
    if not value:
        return False
    return value.startswith('gAAAAA')
