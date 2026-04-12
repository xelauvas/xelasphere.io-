"""System-wide backpressure semaphores.

Uses stdlib threading.BoundedSemaphore which is cooperatively yielded
by gevent's monkey patch (so green threads don't all block).
"""
import contextlib
import os
from threading import BoundedSemaphore

LLM_CAP = int(os.getenv('BACKPRESSURE_LLM_SLOTS', '8'))
TTS_CAP = int(os.getenv('BACKPRESSURE_TTS_SLOTS', '4'))
VOICE_CAP = int(os.getenv('BACKPRESSURE_VOICE_SLOTS', '3'))

LLM_SLOTS = BoundedSemaphore(LLM_CAP)
LLM_SLOTS._initial_value = LLM_CAP

TTS_SLOTS = BoundedSemaphore(TTS_CAP)
TTS_SLOTS._initial_value = TTS_CAP

VOICE_SLOTS = BoundedSemaphore(VOICE_CAP)
VOICE_SLOTS._initial_value = VOICE_CAP


@contextlib.contextmanager
def acquire_or_503(sem: BoundedSemaphore, timeout: float = 5.0):
    """Context manager: acquire a slot within timeout, else yield False.

    Usage:
        with acquire_or_503(LLM_SLOTS, timeout=5) as ok:
            if not ok:
                return jsonify({'error': 'Server busy'}), 503
            ...stream...
    """
    acquired = sem.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            sem.release()


def slot_counts() -> dict:
    """Return capacity + currently available slots for each semaphore."""
    return {
        'llm': {'capacity': LLM_CAP, 'available': LLM_SLOTS._value},
        'tts': {'capacity': TTS_CAP, 'available': TTS_SLOTS._value},
        'voice': {'capacity': VOICE_CAP, 'available': VOICE_SLOTS._value},
    }
