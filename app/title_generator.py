import logging
import re
from typing import Set, Optional, List
from .config import Config
from .utilities import send_metadata_prompt


class TitleGenerator:
    """Handles title generation using the metadata model stack
    (Gemini primary, Groq 70B fallback)."""

    MAX_TITLE_LENGTH = 50
    MAX_SOURCE_CHARS = 1200

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Define common stopwords
        self.stopwords: Set[str] = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
            'for', 'of', 'with', 'by'
        }

    def _clean_text(self, text: str) -> str:
        """Clean text by removing special characters and extra spaces."""
        text = re.sub(r'[^\w\s-]', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _strip_leading_label(self, text: str) -> str:
        """
        Remove leading labels like 'Title:'/'Headline:' to avoid them in the final title.
        """
        if not text:
            return ""
        return re.sub(r'^(title|headline|summary)\s*[:\-]\s*', '', text, flags=re.IGNORECASE).strip()

    def _extract_keywords(self, text: str, max_words: int = 4) -> List[str]:
        """Extract meaningful keywords from text."""
        words = text.lower().split()
        keywords = [word for word in words if len(word) > 3 and word not in self.stopwords]
        return keywords[:max_words]

    def _generate_summary_prompt(self, text: str) -> str:
        """Generate a prompt for LLM to create a title from user intent."""
        return (
            "You will name a chat session using only the user's request/intent below.\n"
            "Analyze the intent and produce ONE concise title that obeys these rules:\n"
            "- 3 to 6 words\n"
            "- Focus on nouns/key subjects (no filler like 'help me')\n"
            "- No punctuation, emojis, or quotes\n"
            "- Title should capture the topic, not the solution steps\n"
            "Return ONLY the final title text.\n\n"
            f"User intent snippets:\n{text}\n\nTitle:"
        )

    def _fallback_to_keywords(self, text: str, max_words: int = 6, min_words: int = 3) -> str:
        """Generate a fallback title using keyword extraction."""
        keywords = self._extract_keywords(text, max_words=max_words)
        if not keywords or len(keywords) < min_words:
            self.logger.warning("Keyword extraction failed. Returning 'Untitled'.")
            return "Untitled"
        return " ".join(keywords).title()

    def _local_title(self, text: str, max_words: int = 8) -> str:
        """
        Build a short title locally from the provided text without remote calls.
        """
        cleaned = self._clean_text(text)
        if not cleaned:
            return "Untitled"
        # Take the first meaningful sentence
        sentences = re.split(r'[.!?]\s+', cleaned)
        first_sentence = next((s for s in sentences if s.strip()), cleaned)
        words = first_sentence.split()
        if not words:
            words = cleaned.split()
        title_words = words[:max_words]
        title = " ".join(title_words).strip()
        if not title or len(title_words) < 3:
            return "Untitled"
        return title[:self.MAX_TITLE_LENGTH].title()

    def _generate_llm_title(self, text: str) -> Optional[str]:
        """Craft a high-quality title via the metadata model stack."""
        if not text:
            return None

        snippet = text[:self.MAX_SOURCE_CHARS].strip()
        if not snippet:
            return None

        prompt = self._generate_summary_prompt(snippet)

        try:
            response = send_metadata_prompt(
                prompt,
                max_tokens=40,
                temperature=0.3,
                timeout=8,
            )
            if not response:
                return None

            candidate = response.strip().splitlines()[0]
            candidate = self._strip_leading_label(candidate)
            candidate = candidate.strip(' "\'”“’')
            candidate = re.sub(r'\s+', ' ', candidate).strip()
            if not candidate:
                return None

            candidate = candidate[:self.MAX_TITLE_LENGTH].lower()
            return candidate.capitalize()
        except Exception as exc:
            self.logger.warning(f"LLM title generation failed: {exc}")
            return None

    def generate_title(self, response_text: str, max_length: int = MAX_TITLE_LENGTH) -> str:
        """
        Generate a concise title using local heuristics (no external API).

        Args:
            response_text (str): Input text to generate title from
            max_length (int): Maximum length of the title in characters

        Returns:
            str: Generated title in Title Case
        """
        if not response_text:
            self.logger.warning("Input text is empty. Returning 'Untitled'.")
            return "Untitled"

        try:
            cleaned_text = self._strip_leading_label(self._clean_text(response_text))

            llm_title = self._generate_llm_title(cleaned_text)
            if llm_title and llm_title.lower() != 'untitled':
                return llm_title[:max_length]

            # First try local title from text
            title = self._local_title(cleaned_text)
            if title and title.lower() != 'untitled':
                title = self._strip_leading_label(title)
                return title[:max_length]

            # Fall back to keyword extraction
            self.logger.warning("Local title generation fell back to keywords.")
            return self._fallback_to_keywords(cleaned_text)

        except Exception as e:
            self.logger.error(f"Error generating title: {e}")
            # Fall back to a simple cleaned truncation of the input text
            return self._clean_text(response_text)[:max_length].title()


# Singleton instance for reuse
title_generator = TitleGenerator()




def generate_title(response_text: str) -> str:
    """
    Wrapper function for backward compatibility and ease of use.
    """
    return title_generator.generate_title(response_text)
