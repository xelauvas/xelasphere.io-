# app/task_wrapper.py

from typing import Optional, List
from .grog_api import TaskGenerator, TaskOption
from firebase_admin import firestore
import logging
import datetime

logger = logging.getLogger(__name__)

class DefaultTaskGenerator(TaskGenerator):
    """Default task generator implementation that uses the parent API methods"""

    def __init__(self):
        super().__init__()
        logger.info("DefaultTaskGenerator initialized")

    def generate_options(self, user_history: Optional[List[str]] = None) -> List[TaskOption]:
        """Generate task options using the parent's synchronous API method"""
        try:
            # Use parent class's synchronous generate_task_options
            options = super().generate_task_options(user_history=user_history)

            if not options:
                logger.warning("No options returned from API, using fallback options")
                options = self.get_fallback_options()

            # Ensure we have the "More" button if needed
            if len(options) >= 4 and options[-1].title != "More":
                options.append(TaskOption(
                    title="More",
                    subtitle="Load additional tasks",
                    action="fetchMoreOptions"
                ))

            logger.info(f"Successfully generated {len(options)} task options (including More button)")
            return options

        except Exception as e:
            logger.error(f"Error in DefaultTaskGenerator: {e}")
            # Even in case of error, return fallback options with More button
            fallback = self.get_fallback_options()
            if len(fallback) >= 4:
                fallback.append(TaskOption(
                    title="More",
                    subtitle="Load additional tasks",
                    action="fetchMoreOptions"
                ))
            return fallback


class EnhancedPersonalizedTaskGenerator:
    """Enhanced PersonalizedTaskGenerator that handles async operations"""

    def __init__(self, db: firestore.Client, task_generator: TaskGenerator):
        self.db = db  # Firestore client
        self.task_generator = task_generator
        self._options_cache = {}
        self.cache_duration = datetime.timedelta(minutes=5)
        logger.info("EnhancedPersonalizedTaskGenerator initialized successfully")

    async def generate_task_options(self, user_id: Optional[str] = None) -> List[TaskOption]:
        """Generate personalized task options (async version)"""
        try:
            # Check cache if user_id is provided
            if user_id and user_id in self._options_cache:
                options, timestamp = self._options_cache[user_id]
                if datetime.datetime.now() - timestamp < self.cache_duration:
                    logger.debug("Returning options from cache.")
                    return options

            # Fetch user preferences and history from Firestore
            prefs = await self._fetch_user_preferences(user_id)
            user_history = await self._fetch_user_history(user_id) if user_id else None
            logger.debug(f"Preferences: {prefs}, History topics: {user_history}")

            # Generate base options with user history for personalization
            options = self.task_generator.generate_options(user_history=user_history)
            logger.debug(f"Options after generating: {options}")

            if prefs and prefs.get("preferred_categories"):
                # Filter and sort options based on user preferences
                preferred_categories = [cat.lower() for cat in prefs["preferred_categories"]]
                preferred_options = [
                    opt for opt in options
                    if any(cat in opt.title.lower() for cat in preferred_categories)
                ]
                other_options = [
                    opt for opt in options
                    if not any(cat in opt.title.lower() for cat in preferred_categories)
                ]

                # Sort preferred options by difficulty if user has a preference
                if prefs.get("preferred_difficulty"):
                    preferred_difficulty_score = self._difficulty_score(prefs["preferred_difficulty"])
                    preferred_options.sort(
                        key=lambda x: abs(
                            self._difficulty_score(x.difficulty or "Easy") -
                            preferred_difficulty_score
                        )
                    )

                # Combine all options, keeping More button if it exists
                main_options = preferred_options + other_options

                # Ensure 'personalized_options' is defined
                personalized_options = []

                if main_options:
                    if main_options[-1].title == "More":
                        more_button = main_options.pop()
                        personalized_options = main_options[:6] + [more_button]
                    else:
                        personalized_options = main_options[:6]
                        if len(main_options) > 6:
                            personalized_options.append(TaskOption(
                                title="More",
                                subtitle="Load additional tasks",
                                action="fetchMoreOptions"
                            ))
                else:
                    # If 'main_options' is empty, use fallback options
                    personalized_options = self.task_generator.get_fallback_options()

                # Update cache if user_id is provided
                if user_id:
                    self._options_cache[user_id] = (personalized_options, datetime.datetime.now())
                logger.debug(f"Personalized options: {personalized_options}")
                return personalized_options
            else:
                # Handle the case where preferences are not set or 'preferred_categories' is empty
                if options:
                    if options[-1].title == "More":
                        personalized_options = options[:6] + [options[-1]]
                    elif len(options) > 6:
                        personalized_options = options[:6]
                        personalized_options.append(TaskOption(
                            title="More",
                            subtitle="Load additional tasks",
                            action="fetchMoreOptions"
                        ))
                    else:
                        personalized_options = options

                    # Update cache if user_id is provided
                    if user_id:
                        self._options_cache[user_id] = (personalized_options, datetime.datetime.now())
                    return personalized_options
                else:
                    # If options are empty, return fallback options
                    logger.warning("No options generated; returning fallback options")
                    return self.task_generator.get_fallback_options()

        except Exception as e:
            logger.error(f"Error generating personalized tasks for user {user_id}: {e}")
            return self.task_generator.get_fallback_options()

    async def _fetch_user_preferences(self, user_id: str) -> Optional[dict]:
        """Fetch user preferences from Firestore."""
        try:
            sessions_ref = self.db.collection('users').document(user_id).collection('chat_sessions')
            latest_session = sessions_ref.order_by('last_updated', direction=firestore.Query.DESCENDING).limit(1).get()
            if latest_session:
                preferences = latest_session[0].to_dict().get('preferences', [])
                logger.info(f"Fetched preferences from latest session for user {user_id}: {preferences}")
                return {
                    "preferred_categories": preferences,
                    "preferred_difficulty": "Medium"  # Set default or derive if stored
                }
            logger.warning(f"No sessions found for user {user_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching preferences for user {user_id}: {e}")
            return None

    async def _fetch_user_history(self, user_id: str) -> Optional[List[str]]:
        """Fetch recent topics/interests from user's chat history."""
        try:
            if not user_id:
                return None

            sessions_ref = self.db.collection('users').document(user_id).collection('chat_sessions')
            # Get the 5 most recent sessions
            recent_sessions = sessions_ref.order_by(
                'last_updated', direction=firestore.Query.DESCENDING
            ).limit(5).get()

            topics = []
            for session_doc in recent_sessions:
                session_data = session_doc.to_dict()
                # Get session name/title as a topic indicator
                session_name = session_data.get('name', '')
                if session_name and session_name not in ['New Chat', 'Untitled', '']:
                    topics.append(session_name)

                # Also extract first user message as topic hint
                messages = session_data.get('messages', [])
                for msg in messages[:2]:  # Check first 2 messages
                    if msg.get('sender', '').lower() == 'user':
                        content = msg.get('content', '') or msg.get('prompt', '')
                        if content and len(content) < 100:  # Short messages are likely topic indicators
                            # Clean and truncate
                            clean_topic = content.strip()[:50]
                            if clean_topic and clean_topic not in topics:
                                topics.append(clean_topic)
                        break

            # Return unique topics, max 5
            unique_topics = list(dict.fromkeys(topics))[:5]
            logger.debug(f"Fetched user history topics for {user_id}: {unique_topics}")
            return unique_topics if unique_topics else None

        except Exception as e:
            logger.error(f"Error fetching user history for {user_id}: {e}")
            return None

    async def save_user_preferences(self, user_id: str, preferences: dict):
        """Save or update user preferences in Firestore."""
        try:
            self.db.collection('user_preferences').document(user_id).set(preferences)
            logger.info(f"Successfully saved preferences for user {user_id}")
        except Exception as e:
            logger.error(f"Error saving preferences for user {user_id}: {e}")

    @staticmethod
    def _difficulty_score(difficulty: str) -> int:
        """Convert difficulty string to numeric score for comparison."""
        difficulty_map = {
            'Easy': 1,
            'Medium': 2,
            'Hard': 3,
            'Expert': 4
        }
        return difficulty_map.get(difficulty, 1)

def create_task_generator(db: firestore.Client) -> EnhancedPersonalizedTaskGenerator:
    """Factory function to create a properly initialized TaskGenerator."""
    try:
        base_generator = DefaultTaskGenerator()
        return EnhancedPersonalizedTaskGenerator(db, base_generator)
    except Exception as e:
        logger.error(f"Error creating task generator: {e}")
        raise
