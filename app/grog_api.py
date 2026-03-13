# task_generator.py

import requests
import json
import re
import time
import logging
import random
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
from .config import Config

# Set logging level to INFO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TaskCategory(Enum):
    """Enumeration of available task categories."""
    TECHNOLOGY = "Technology and Coding"
    PERSONAL = "Personal Development"
    CREATIVE = "Creative Arts"
    HEALTH = "Health and Wellness"
    SOCIAL = "Social Activities"
    PROBLEM_SOLVING = "Problem Solving and Logic"


@dataclass
class TaskTemplate:
    """Data structure for task templates."""
    themes: List[str]
    difficulty_levels: List[str]
    estimated_times: List[str]
    action_templates: List[str]


class CircuitBreaker:
    """Implementation of a simple Circuit Breaker pattern to prevent repeated failures."""
    def __init__(self, failure_threshold: int, timeout: int):
        """
        Args:
            failure_threshold (int): Number of consecutive failures before opening circuit.
            timeout (int): Timeout in seconds before attempting requests again.
        """
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.last_failure_time = 0

    def record_failure(self) -> None:
        """Record a failure occurrence and open the circuit if threshold is exceeded."""
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.last_failure_time = time.time()
            logger.warning("Circuit breaker opened due to repeated failures.")

    def record_success(self) -> None:
        """Record a successful API call and reset the circuit breaker state."""
        self.failure_count = 0

    def is_open(self) -> bool:
        """Check if the circuit breaker is currently open."""
        if self.failure_count >= self.failure_threshold:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.timeout:
                # Reset after timeout to allow new attempts
                logger.info("Circuit breaker timeout elapsed. Closing the circuit.")
                self.failure_count = 0
                return False
            return True
        return False


# Available icons for task options (Feather icons)
TASK_ICONS = [
    'edit',      # Writing, drafts, emails
    'code',      # Programming, debugging
    'image',     # Images, photos, design
    'search',    # Research, finding
    'info',      # Explaining, analyzing
    'calendar',  # Planning, scheduling
    'activity',  # Fitness, workout
    'dollar',    # Money, budget, finance
    'globe',     # Travel, world
    'gift',      # Presents, gifts
    'film',      # Movies, videos
    'home',      # House, room, organization
    'book',      # Learning, reading, study
    'heart',     # Wellness, health
    'zap',       # Motivation, energy
    'coffee',    # Lifestyle, relaxation
    'music',     # Music, audio
    'sun',       # Weather, mood
    'star',      # General, default
    'sparkles',  # Creative, brainstorm
    'lightbulb', # Ideas, thinking
    'message',   # Communication, chat
    'more',      # More options
]

@dataclass
class TaskOption:
    """Data structure for individual task options."""
    title: str
    subtitle: str
    difficulty: Optional[str] = None
    estimated_time: Optional[str] = None
    action: Optional[str] = None
    icon: Optional[str] = 'star'  # Default icon

    def to_dict(self) -> Dict[str, Any]:
        """Convert TaskOption to dictionary format."""
        return {
            'title': self.title,
            'subtitle': self.subtitle,
            'difficulty': self.difficulty,
            'estimated_time': self.estimated_time,
            'action': self.action,
            'icon': self.icon or 'star'
        }


DEPRECATED_MODELS = {
    "llama-3.2-90b-text-preview": "llama-3.2-90b-vision-preview"
}


class TaskGenerator:
    """
    Class that uses a language model API to generate dynamic tasks.
    Features:
    - Multiple model fallback
    - Circuit breaker for error handling
    - No caching (always fetch fresh tasks)
    - Variation on "More" button for fresh tasks
    """

    def __init__(self):
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        # Task generation models: primary and fallbacks
        # Models with "/" prefix use OpenRouter, others use Groq
        self.models = [
            "llama-3.1-8b-instant",           # Groq - primary
            "llama-3.3-70b-versatile",        # Groq - fallback
        ]
        self.groq_headers = {
            "Authorization": f"Bearer {Config.GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        self.openrouter_headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": Config.OPENROUTER_SITE_URL or "https://xelasphere.io",
            "X-Title": Config.OPENROUTER_APP_NAME or "XelaSphere"
        }
        # Keep backwards compatibility
        self.headers = self.groq_headers
        self._current_num_tasks = 6  # Track current number of tasks to generate
        self.task_template = TaskTemplate(
            themes=[
                "exploring new skills",
                "boosting creativity",
                "enhancing well-being",
                "social engagement",
                "developing logical thinking",
                "creating projects"
            ],
            difficulty_levels=["Easy", "Intermediate", "Challenging"],
            estimated_times=["15-30 mins", "30-60 mins", "1-2 hours", "2-3 hours"],
            action_templates=[
                "Research and gather information on the topic.",
                "Write a short essay or article explaining the concept.",
                "Create a project prototype using tools or code.",
                "Brainstorm creative ideas to solve a problem.",
                "Engage in a physical activity related to the theme.",
                "Collaborate with someone and work on a task together.",
                "Analyze and summarize key findings.",
                "Present your work to a friend or community group."
            ]
        )

    def generate_chatgpt_like_prompt(self,
                                     category: Optional[TaskCategory] = None,
                                     theme: Optional[str] = None,
                                     num_tasks: int = 3,
                                     num_steps: int = None,
                                     force_new_tasks: bool = False,
                                     user_history: Optional[List[str]] = None) -> str:
        """
        Generate a prompt to request tasks from the AI model.

        Args:
            category (Optional[TaskCategory]): If provided, focus tasks on this category.
            theme (Optional[str]): If provided, use this theme instead of a random one.
            num_tasks (int): Number of tasks to generate. Defaults to 3.
            num_steps (int): Number of steps in each task. If None, choose randomly.
            force_new_tasks (bool): If True, add variations to get fresh tasks.
            user_history (Optional[List[str]]): Recent user topics/interests from chat history.

        Returns:
            str: The generated prompt string.
        """
        if num_steps is None:
            num_steps = random.randint(3, 6)

        if force_new_tasks:
            category = random.choice(list(TaskCategory))
            theme = random.choice(self.task_template.themes)

        selected_category = category or random.choice(list(TaskCategory))
        selected_theme = theme or random.choice(self.task_template.themes)

        # Build history context if available
        history_context = ""
        if user_history and len(user_history) > 0:
            topics = ", ".join(user_history[:5])  # Use up to 5 recent topics
            history_context = f"The user has recently been interested in: {topics}. Tailor some suggestions to these interests. "

        unique_marker = f"(RequestID: {random.randint(1000, 9999)})"
        repeat_avoidance = "Do not repeat previous suggestions. " if force_new_tasks else ""

        # Available icons for the AI to choose from
        icons_list = ", ".join(TASK_ICONS[:-1])  # Exclude 'more' as that's reserved

        return f"""Generate a JSON array of {num_tasks} conversation starter buttons for an AI chat app. {unique_marker}

{history_context}{repeat_avoidance}

Use these 3 STYLES (mix them):

1. CONVERSATIONAL (questions the user might ask):
   - "What should I cook?" / "Help me plan my week" / "What should I watch?"

2. ACTION-ORIENTED (things to do):
   - "Write an email" / "Summarize this" / "Brainstorm ideas"

3. PROBLEM-SOLVING (user states a problem):
   - "I'm bored" / "I can't decide" / "I'm stuck" / "I need motivation"

Each item must be a JSON object with:
- 'title': Short, conversational phrase (2-5 words). Use "I" or question format.
- 'subtitle': Brief benefit or context (3-7 words)
- 'difficulty': "Easy"
- 'estimated_time': "2 mins" or "5 mins"
- 'action': The full natural message to send, e.g. "I'm bored. What should I do?"
- 'icon': Pick the best matching icon from: {icons_list}
  (edit=writing, code=programming, image=design, search=research, info=explaining, calendar=planning, activity=fitness, dollar=finance, globe=travel, gift=presents, film=movies, home=organization, book=learning, heart=wellness, zap=motivation, coffee=lifestyle, music=audio, sun=mood, star=general, sparkles=creative, lightbulb=ideas, message=chat)

Theme hint: {selected_theme} / {selected_category.value}

Return ONLY the JSON array, no explanation."""

    def fetch_options_from_api(self, model: str, prompt: str) -> Optional[List[TaskOption]]:
        """
        Fetch options from the API using a specific model and prompt.
        Automatically detects provider (Groq vs OpenRouter) based on model name.
        """
        if self.circuit_breaker.is_open():
            logger.warning("Circuit breaker is open. Skipping API call.")
            return None

        # Detect provider based on model name (OpenRouter models have "/" in name)
        is_openrouter = "/" in model
        if is_openrouter:
            url = Config.OPENROUTER_API_URL
            headers = self.openrouter_headers
            logger.debug(f"Using OpenRouter for model: {model}")
        else:
            url = Config.API_URL
            headers = self.groq_headers
            logger.debug(f"Using Groq for model: {model}")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 1.0,
        }

        try:
            logger.debug(f"Attempting request to {url} with model: {model}")
            response = requests.post(url, json=payload, headers=headers, timeout=15)

            if response.status_code == 200:
                try:
                    response_json = response.json()
                except ValueError as decode_err:
                    snippet = response.text[:500] if response.text else ''
                    logger.error(f"JSON decode error from {model}: {decode_err}. Raw snippet: {snippet}")
                    self.circuit_breaker.record_failure()
                else:
                    options = self.parse_api_response(response_json)
                    if options:
                        self.circuit_breaker.record_success()
                        logger.info(f"Successfully fetched {len(options)} options from {model}")
                        return options
                    self.circuit_breaker.record_failure()
                    logger.warning(f"No valid options parsed from {model}")
            else:
                self.circuit_breaker.record_failure()
                self.log_http_error_details(response)
                
                # Handle model deprecation/not found errors
                error_message = response.text.lower()
                if response.status_code == 400 and "model_decommissioned" in error_message:
                    logger.warning(f"Model {model} is decommissioned.")
                    self.handle_deprecated_model(model)
                elif "model_not_found" in error_message:
                    logger.error(f"Model {model} not found - removing from available models")
                    if model in self.models:
                        self.models.remove(model)

        except requests.Timeout:
            self.circuit_breaker.record_failure()
            logger.error(f"Request to {model} timed out ({url})")
        except requests.RequestException as e:
            self.circuit_breaker.record_failure()
            logger.error(f"API request failed to {url}: {str(e)}")
        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.error(f"Unexpected error with {model}: {str(e)}")

        return None

    def generate_options(self, user_history: Optional[List[str]] = None) -> List[TaskOption]:
        """Simple wrapper to generate task options with user history."""
        return self.generate_task_options(user_history=user_history)

    def generate_task_options(self,
                              category: Optional[TaskCategory] = None,
                              theme: Optional[str] = None,
                              num_tasks: int = 6,
                              num_steps: int = None,
                              include_more_option: bool = True,
                              force_new_tasks: bool = False,
                              user_history: Optional[List[str]] = None) -> List[TaskOption]:
        """
        Generate a list of dynamic task options. Always fetches new tasks from the API (no caching).

        Args:
            category (Optional[TaskCategory]): Preferred category.
            theme (Optional[str]): Preferred theme.
            num_tasks (int): Number of tasks to generate.
            num_steps (int): Number of steps per task. If None, randomly chosen.
            include_more_option (bool): Whether to append a "More" option at the end.
            force_new_tasks (bool): If True, add variations and instructions to get fresh tasks.
            user_history (Optional[List[str]]): Recent user topics/interests from chat history.

        Returns:
            List[TaskOption]: A list of TaskOption objects.
        """
        # If forcing new tasks, randomize steps to ensure difference
        # Only randomize num_tasks if using default value (6)
        if force_new_tasks:
            if num_tasks == 6:  # Default value, can randomize
                num_tasks = random.randint(5, 7)
            num_steps = random.randint(3, 6)

        # Store num_tasks for use in format_options
        self._current_num_tasks = num_tasks

        prompt = self.generate_chatgpt_like_prompt(
            category, theme, num_tasks, num_steps,
            force_new_tasks=force_new_tasks,
            user_history=user_history
        )

        # Try each model
        for model in list(self.models):
            options = self.fetch_options_from_api(model, prompt)
            if options:
                if not include_more_option:
                    options = [opt for opt in options if opt.title.lower() != "more"]
                return options

        # If all fail, return fallback
        return self.get_fallback_options(num_tasks=num_tasks, include_more_option=include_more_option)

    def parse_api_response(self, response_json: Dict[str, Any]) -> Optional[List[TaskOption]]:
        """
        Parse the API response to extract a list of TaskOption objects.

        Args:
            response_json (dict): The JSON response from the API.

        Returns:
            Optional[List[TaskOption]]: Parsed TaskOption objects or None if parsing fails.
        """
        try:
            if not isinstance(response_json, dict) or 'choices' not in response_json:
                raise ValueError("Invalid response structure")

            choices = response_json.get('choices', [])
            if not choices:
                raise ValueError("No choices in response")

            content = choices[0].get('message', {}).get('content', '')
            if not content:
                raise ValueError("No content in response")

            json_str = self.extract_json_string(content)
            if not json_str:
                raise ValueError("No valid JSON found in response")

            raw_options = self.sanitize_and_load_json(json_str)
            if not raw_options:
                raise ValueError("Failed to parse JSON data")

            if isinstance(raw_options, dict):
                raw_options = [raw_options]

            return self.format_options(raw_options)

        except Exception as e:
            logger.error(f"Error parsing API response: {e}")
            return None

    # Category keywords for detecting user interests from messages
    CATEGORY_KEYWORDS = {
        'coding': ['code', 'python', 'javascript', 'java', 'function', 'class', 'api', 'bug', 'error',
                   'debug', 'programming', 'developer', 'script', 'html', 'css', 'react', 'node',
                   'database', 'sql', 'git', 'deploy', 'server', 'frontend', 'backend', 'algorithm'],
        'writing': ['write', 'email', 'letter', 'essay', 'article', 'blog', 'content', 'copy',
                    'grammar', 'proofread', 'edit', 'draft', 'message', 'text', 'document'],
        'planning': ['plan', 'schedule', 'organize', 'project', 'task', 'goal', 'deadline',
                     'timeline', 'roadmap', 'strategy', 'meeting', 'event', 'trip', 'travel'],
        'learning': ['learn', 'explain', 'understand', 'teach', 'tutorial', 'how to', 'what is',
                     'concept', 'study', 'course', 'education', 'skill', 'knowledge'],
        'creativity': ['idea', 'brainstorm', 'creative', 'design', 'innovate', 'imagine',
                       'inspiration', 'gift', 'decide', 'choose', 'option', 'alternative'],
        'lifestyle': ['cook', 'recipe', 'food', 'movie', 'show', 'watch', 'read', 'book',
                      'music', 'hobby', 'fun', 'entertainment', 'bored', 'weekend'],
        'wellness': ['stress', 'relax', 'meditation', 'health', 'exercise', 'sleep', 'anxiety',
                     'motivation', 'productivity', 'focus', 'energy', 'tired', 'stuck'],
    }

    @classmethod
    def detect_categories(cls, text: str) -> List[str]:
        """Detect categories from a text message."""
        if not text:
            return []

        text_lower = text.lower()
        detected = []

        for category, keywords in cls.CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    if category not in detected:
                        detected.append(category)
                    break

        return detected

    @staticmethod
    def _get_personalized_options(categorized_options: dict, user_preferences: dict, num_tasks: int) -> List[TaskOption]:
        """Select options based on user preferences."""
        import random

        if not user_preferences or not user_preferences.get('categories'):
            # No preferences - pick randomly from all categories
            all_options = []
            for opts in categorized_options.values():
                all_options.extend(opts)
            random.shuffle(all_options)
            return all_options[:num_tasks]

        # Sort categories by usage count (descending)
        category_counts = user_preferences.get('categories', {})
        sorted_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)

        selected = []
        used_titles = set()

        # First, pick from top categories
        for category, count in sorted_categories:
            if len(selected) >= num_tasks:
                break
            if category in categorized_options:
                for opt in categorized_options[category]:
                    if opt.title not in used_titles and len(selected) < num_tasks:
                        selected.append(opt)
                        used_titles.add(opt.title)
                        break

        # Fill remaining slots with random options from other categories
        if len(selected) < num_tasks:
            all_options = []
            for opts in categorized_options.values():
                all_options.extend([o for o in opts if o.title not in used_titles])
            random.shuffle(all_options)
            for opt in all_options:
                if len(selected) >= num_tasks:
                    break
                selected.append(opt)

        return selected

    @classmethod
    def get_fallback_options(cls, num_tasks: int = 6, include_more_option: bool = True,
                             user_preferences: dict = None) -> List[TaskOption]:
        """
        Provide personalized fallback options based on user preferences.

        Args:
            num_tasks (int): Number of tasks to return.
            include_more_option (bool): Whether to include a "More" option.
            user_preferences (dict): User's category preferences from Firebase.

        Returns:
            List[TaskOption]: A list of TaskOption objects.
        """
        import random

        # Options organized by category for smart personalization
        categorized_options = {
            'coding': [
                TaskOption(
                    title="Help with my code",
                    subtitle="Debug, explain, or improve",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('I need help with my code')",
                    icon="code"
                ),
                TaskOption(
                    title="Explain this error",
                    subtitle="Understand what went wrong",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Can you explain this error I\\'m getting?')",
                    icon="info"
                ),
                TaskOption(
                    title="Review my code",
                    subtitle="Get feedback and suggestions",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('Can you review my code and suggest improvements?')",
                    icon="search"
                ),
                TaskOption(
                    title="How do I build this?",
                    subtitle="Step-by-step guidance",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('How do I build this feature?')",
                    icon="lightbulb"
                ),
            ],
            'writing': [
                TaskOption(
                    title="Write an email",
                    subtitle="Professional, casual, or creative",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Help me write an email')",
                    icon="edit"
                ),
                TaskOption(
                    title="Improve my writing",
                    subtitle="Make it clearer and better",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Can you help improve my writing?')",
                    icon="edit"
                ),
                TaskOption(
                    title="Summarize this",
                    subtitle="Get the key points fast",
                    difficulty="Easy",
                    estimated_time="2 mins",
                    action="sendMessage('I need help summarizing something')",
                    icon="book"
                ),
            ],
            'planning': [
                TaskOption(
                    title="Help me plan my week",
                    subtitle="Get organized in 5 minutes",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Help me plan my week and get organized')",
                    icon="calendar"
                ),
                TaskOption(
                    title="Break down this project",
                    subtitle="Step-by-step action plan",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('Help me break down this project into steps')",
                    icon="calendar"
                ),
                TaskOption(
                    title="Plan a trip",
                    subtitle="Your next adventure starts here",
                    difficulty="Easy",
                    estimated_time="15 mins",
                    action="sendMessage('Help me plan a trip')",
                    icon="globe"
                ),
            ],
            'learning': [
                TaskOption(
                    title="Explain it simply",
                    subtitle="Make complex things clear",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Can you explain something to me in simple terms?')",
                    icon="info"
                ),
                TaskOption(
                    title="Learn something new",
                    subtitle="Pick up a skill today",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('I want to learn something new. What do you suggest?')",
                    icon="book"
                ),
                TaskOption(
                    title="How does this work?",
                    subtitle="Understand the concept",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('Can you explain how this works?')",
                    icon="lightbulb"
                ),
            ],
            'creativity': [
                TaskOption(
                    title="Brainstorm ideas",
                    subtitle="Creative thinking partner",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('Help me brainstorm some ideas')",
                    icon="sparkles"
                ),
                TaskOption(
                    title="I can't decide",
                    subtitle="Help me make a choice",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('I can\\'t decide between some options. Help me choose')",
                    icon="lightbulb"
                ),
                TaskOption(
                    title="What's a good gift?",
                    subtitle="Find the perfect present",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('I need help finding a good gift for someone')",
                    icon="gift"
                ),
            ],
            'lifestyle': [
                TaskOption(
                    title="What should I cook?",
                    subtitle="Quick recipes for tonight",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('What should I cook tonight? Give me some quick recipe ideas')",
                    icon="coffee"
                ),
                TaskOption(
                    title="What should I watch?",
                    subtitle="Movie or show recommendations",
                    difficulty="Easy",
                    estimated_time="2 mins",
                    action="sendMessage('What should I watch tonight? Give me some recommendations')",
                    icon="film"
                ),
                TaskOption(
                    title="I'm bored",
                    subtitle="Get activity suggestions",
                    difficulty="Easy",
                    estimated_time="2 mins",
                    action="sendMessage('I\\'m bored. What should I do?')",
                    icon="sun"
                ),
            ],
            'wellness': [
                TaskOption(
                    title="I'm stressed",
                    subtitle="Quick relaxation techniques",
                    difficulty="Easy",
                    estimated_time="5 mins",
                    action="sendMessage('I\\'m feeling stressed. Help me relax')",
                    icon="heart"
                ),
                TaskOption(
                    title="I need motivation",
                    subtitle="Get a productivity boost",
                    difficulty="Easy",
                    estimated_time="2 mins",
                    action="sendMessage('I need some motivation to get started')",
                    icon="zap"
                ),
                TaskOption(
                    title="I'm stuck",
                    subtitle="Work through a problem",
                    difficulty="Easy",
                    estimated_time="10 mins",
                    action="sendMessage('I\\'m stuck on something and need help thinking through it')",
                    icon="lightbulb"
                ),
            ],
        }

        # Get personalized options based on user preferences
        final_options = cls._get_personalized_options(
            categorized_options, user_preferences, num_tasks
        )

        if include_more_option:
            final_options.append(TaskOption(
                title="More",
                subtitle="Load new tasks",
                action="fetchMoreOptions()",
                icon="more"
            ))

        return final_options

    def extract_json_string(self, text: str) -> Optional[str]:
        """
        Extract a JSON string (array or object) from the text.

        Args:
            text (str): The text from which to extract JSON.

        Returns:
            Optional[str]: The extracted JSON string or None if not found.
        """
        try:
            json_array_match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
            if json_array_match:
                return json_array_match.group(0)

            json_object_match = re.search(r'\{.*?\}', text, re.DOTALL)
            if json_object_match:
                return json_object_match.group(0)

        except Exception as e:
            logger.error(f"Error extracting JSON: {e}")
        return None

    def sanitize_and_load_json(self, json_str: str) -> Optional[Any]:
        """
        Sanitize and load a JSON string into Python objects.

        Args:
            json_str (str): The raw JSON string to parse.

        Returns:
            Optional[Any]: The loaded JSON object or None if parsing fails.
        """
        try:
            json_str = re.sub(r'\\(?![\"\\/bfnrtu])', r'', json_str)
            json_str = json_str.strip()
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in JSON processing: {e}")
            return None

    def format_options(self, raw_options: List[Dict]) -> List[TaskOption]:
        """
        Format raw option dictionaries into TaskOption objects with validation.
        Ensures that the action field always includes both the title and subtitle.
        """

        formatted_options = []
        for option in raw_options:
            try:
                title = str(option.get("title", "")).strip().title()
                subtitle = str(option.get("subtitle", "")).strip().capitalize()
                difficulty = str(option.get("difficulty", "")).strip().capitalize()
                estimated_time = str(option.get("estimated_time", "")).strip()

                # Parse and validate icon - use 'star' as default
                icon = str(option.get("icon", "star")).strip().lower()
                if icon not in TASK_ICONS:
                    icon = "star"  # Default to star if invalid icon

                # Minimal validation checks
                if not title or not subtitle:
                    continue
                if len(subtitle) <= len(title) + 10 or len(subtitle) > 120:
                    continue

                # Ensure action always includes both title and subtitle.
                action = f"sendMessage('{title}: {subtitle}')"

                formatted_options.append(TaskOption(
                    title=title,
                    subtitle=subtitle,
                    difficulty=difficulty,
                    estimated_time=estimated_time,
                    action=action,
                    icon=icon
                ))

            except Exception as e:
                logger.error(f"Error formatting option: {e}")
                continue

        # Use the stored num_tasks value (defaults to 6 if not set)
        target_count = getattr(self, '_current_num_tasks', 6)

        # If we have fewer than target tasks, fill up with fallback
        while len(formatted_options) < target_count:
            fallback = self.get_fallback_options(num_tasks=target_count, include_more_option=False)
            for opt in fallback:
                if len(formatted_options) < target_count and not any(o.title == opt.title for o in formatted_options):
                    formatted_options.append(opt)

        # Ensure exactly target_count tasks plus one "More"
        formatted_options = formatted_options[:target_count]
        formatted_options.append(TaskOption(
            title="More",
            subtitle="Load new tasks",
            action="fetchMoreOptions()",
            icon="more"
        ))

        return formatted_options


    def log_http_error_details(self, response: requests.Response) -> None:
        """
        Log details about an HTTP error response.

        Args:
            response (requests.Response): The HTTP response to log.
        """
        try:
            logger.error("HTTP Error Details:")
            logger.error(f"Status: {response.status_code}")
            logger.error(f"Headers: {dict(response.headers)}")
            logger.error(f"Content: {response.text}")
            if "model_decommissioned" in response.text.lower():
                logger.warning("Decommissioned model detected in response.")
        except Exception as e:
            logger.error(f"Error logging HTTP details: {e}")

    def handle_deprecated_model(self, model: str):
        """
        Handle deprecated models by attempting to replace them with a known good alternative.

        Args:
            model (str): The deprecated model name.
        """
        alternative = DEPRECATED_MODELS.get(model)
        if alternative:
            logger.info(f"Found replacement for deprecated model {model}: {alternative}")
            if self.validate_model(alternative):
                logger.info(f"Replacing {model} with validated model {alternative}.")
                if model in self.models:
                    self.models.remove(model)
                self.models.append(alternative)
            else:
                logger.warning(f"Replacement model {alternative} is not valid. Removing {model}.")
                if model in self.models:
                    self.models.remove(model)
        else:
            logger.warning(f"No alternative found for deprecated model {model}. Removing from the list.")
            if model in self.models:
                self.models.remove(model)

    def validate_model(self, model: str) -> bool:
        """
        Validate a model by testing a simple request.

        Args:
            model (str): The model name to validate.

        Returns:
            bool: True if the model works, False otherwise.
        """
        test_prompt = "This is a test prompt."
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": test_prompt}],
            "max_tokens": 10,
            "temperature": 0.7,
        }
        try:
            response = requests.post(Config.API_URL, json=payload, headers=self.headers, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Validation failed for model {model}: {e}")
            return False
