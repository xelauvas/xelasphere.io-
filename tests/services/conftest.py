"""
conftest.py for tests/services/

Breaks the circular import that arises when importing app.services.chat_engine
directly (chat_engine → app.blueprints.shared → blueprints/__init__.py →
chat_routes → chat_engine).  We stub the blueprint layer before collection.
"""

import sys
from unittest.mock import MagicMock

# Only install stubs if they haven't been set up by the root conftest already.
_STUBS = {
    "app.blueprints": None,
    "app.blueprints.shared": None,
}

for mod_name in list(_STUBS):
    if mod_name not in sys.modules:
        mock = MagicMock()
        # Provide the specific names that chat_engine pulls from shared
        if mod_name == "app.blueprints.shared":
            mock.chat_session = MagicMock()
            mock.ModelProviderError = type("ModelProviderError", (Exception,), {})
            mock.TokenLimitError = type("TokenLimitError", (Exception,), {})
            mock.GUEST_MESSAGE_LIMIT = 10
            mock.GUEST_BANNER_THRESHOLD = 8
        sys.modules[mod_name] = mock
