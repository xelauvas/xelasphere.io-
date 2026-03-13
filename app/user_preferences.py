from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from firebase_admin import firestore
import logging
import datetime
from collections import Counter
from enum import Enum
from .grog_api import TaskOption, TaskGenerator

logger = logging.getLogger(__name__)

@dataclass
class UserPreferences:
    """Data structure for user preferences with validation"""
    user_id: str
    preferred_categories: List[str]
    preferred_difficulty: str
    preferred_duration: str
    completion_rate: float
    total_completed: int
    total_started: int
    engagement_score: float
    last_updated: datetime
    status: str

    def __post_init__(self):
        """Validate fields after initialization"""
        if not self.user_id:
            raise ValueError("user_id cannot be empty")
        if self.completion_rate < 0 or self.completion_rate > 1:
            raise ValueError("completion_rate must be between 0 and 1")
        if self.total_completed < 0:
            raise ValueError("total_completed cannot be negative")
        if self.total_started < 0:
            raise ValueError("total_started cannot be negative")
        if self.total_completed > self.total_started:
            raise ValueError("total_completed cannot exceed total_started")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserPreferences':
        """Create UserPreferences instance from dictionary with type conversion"""
        try:
            return cls(
                user_id=str(data.get('user_id', '')),
                preferred_categories=list(data.get('preferred_categories', [])),
                preferred_difficulty=str(data.get('preferred_difficulty', 'Easy')),
                preferred_duration=str(data.get('preferred_duration', '30-60 mins')),
                completion_rate=float(data.get('completion_rate', 0.0)),
                total_completed=int(data.get('total_completed', 0)),
                total_started=int(data.get('total_started', 0)),
                engagement_score=float(data.get('engagement_score', 0.0)),
                last_updated=data.get('last_updated', datetime.now()),
                status=str(data.get('status', 'active'))
            )
        except (ValueError, TypeError) as e:
            raise ValueError(f"Error converting data: {e}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert UserPreferences to dictionary with validation"""
        return {
            'user_id': self.user_id,
            'preferred_categories': list(self.preferred_categories),
            'preferred_difficulty': self.preferred_difficulty,
            'preferred_duration': self.preferred_duration,
            'completion_rate': float(self.completion_rate),
            'total_completed': int(self.total_completed),
            'total_started': int(self.total_started),
            'engagement_score': float(self.engagement_score),
            'last_updated': self.last_updated,
            'status': self.status
        }

class UserPreferencesManager:
    """Manages user preferences in Firebase with improved error handling and caching"""
    
    def __init__(self, db: firestore.Client):
        self.db = db
        self.preferences_collection = db.collection('user_preferences')
        self._cache: Dict[str, tuple[UserPreferences, datetime]] = {}
        self.cache_duration = datetime.timedelta(minutes=5)

    def get_user_preferences(self, user_id: str) -> Optional[UserPreferences]:
        try:
            if user_id in self._cache:
                prefs, timestamp = self._cache[user_id]
                if datetime.now() - timestamp < self.cache_duration:
                    return prefs

            doc_ref = self.preferences_collection.document(user_id)
            doc = doc_ref.get()
            
            if doc.exists:
                prefs = UserPreferences.from_dict(doc.to_dict())
                self._cache[user_id] = (prefs, datetime.now())
                return prefs
            return None  # Explicitly return None if no preferences exist

        except Exception as e:
            logger.error(f"Error retrieving user preferences for {user_id}: {e}")
            return None  # Ensure None is returned on error


    async def update_preferences(self, user_id: str, task_options: List['TaskOption']) -> None:
        """Update user preferences with transaction support"""
        try:
            @firestore.transactional
            def update_transaction(transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                current_prefs = UserPreferences.from_dict(doc.to_dict()) if doc.exists else None

                # Calculate new category preferences
                categories = [option.title.split(":")[0].strip() for option in task_options]
                category_counts = Counter(categories)
                most_common_categories = [cat for cat, _ in category_counts.most_common(3)]
                
                # Prepare new preference data
                new_prefs = {
                    'user_id': user_id,
                    'preferred_categories': most_common_categories,
                    'last_updated': firestore.SERVER_TIMESTAMP,
                    'status': 'active'
                }
                
                if current_prefs:
                    new_prefs.update({
                        'preferred_difficulty': current_prefs.preferred_difficulty,
                        'preferred_duration': current_prefs.preferred_duration,
                        'completion_rate': current_prefs.completion_rate,
                        'total_completed': current_prefs.total_completed,
                        'total_started': current_prefs.total_started,
                        'engagement_score': self._calculate_engagement_score(
                            current_prefs.total_completed,
                            current_prefs.total_started
                        )
                    })
                else:
                    new_prefs.update({
                        'preferred_difficulty': 'Easy',
                        'preferred_duration': '30-60 mins',
                        'completion_rate': 0.0,
                        'total_completed': 0,
                        'total_started': 0,
                        'engagement_score': 0.0
                    })
                
                transaction.set(doc_ref, new_prefs, merge=True)
                return new_prefs

            doc_ref = self.preferences_collection.document(user_id)
            new_prefs = self.db.transaction(update_transaction)(doc_ref)

            
            # Update cache
            self._cache[user_id] = (UserPreferences.from_dict(new_prefs), datetime.now())
            logger.info(f"Updated preferences for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error updating user preferences for {user_id}: {e}")
            raise

    async def record_task_start(self, user_id: str, task_id: str) -> None:
        """Record task start with transaction support"""
        try:
            @firestore.transactional
            def start_transaction(transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                if not doc.exists:
                    raise ValueError(f"No preferences found for user {user_id}")
                
                transaction.update(doc_ref, {
                    'total_started': firestore.Increment(1),
                    'last_updated': firestore.SERVER_TIMESTAMP
                })

            doc_ref = self.preferences_collection.document(user_id)
            await self.db.transaction(start_transaction)(doc_ref)
            self._cache.pop(user_id, None)  # Invalidate cache
            
        except Exception as e:
            logger.error(f"Error recording task start for user {user_id}: {e}")
            raise

    async def record_task_completion(self, user_id: str, task_id: str) -> None:
        """Record task completion with transaction support"""
        try:
            @firestore.transactional
            def complete_transaction(transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                if not doc.exists:
                    raise ValueError(f"No preferences found for user {user_id}")
                
                prefs = UserPreferences.from_dict(doc.to_dict())
                new_completed = prefs.total_completed + 1
                new_completion_rate = (new_completed / prefs.total_started) if prefs.total_started > 0 else 0
                
                transaction.update(doc_ref, {
                    'total_completed': firestore.Increment(1),
                    'completion_rate': new_completion_rate,
                    'last_updated': firestore.SERVER_TIMESTAMP
                })

            doc_ref = self.preferences_collection.document(user_id)
            await self.db.transaction(complete_transaction)(doc_ref)
            self._cache.pop(user_id, None)  # Invalidate cache
            
        except Exception as e:
            logger.error(f"Error recording task completion for user {user_id}: {e}")
            raise

    @staticmethod
    def _calculate_engagement_score(completed: int, started: int) -> float:
        """Calculate user engagement score with validation"""
        if started < 0 or completed < 0:
            raise ValueError("Completed and started counts must be non-negative")
        if completed > started:
            raise ValueError("Completed tasks cannot exceed started tasks")
        if started == 0:
            return 0.0
        completion_rate = completed / started
        return min(completion_rate * 100, 100.0)

class PersonalizedTaskGenerator:
    """Enhanced TaskGenerator with user preferences and caching"""
    
    def __init__(self, db: firestore.Client, task_generator: TaskGenerator):
        self.preferences_manager = UserPreferencesManager(db)
        self.task_generator = task_generator
        self._options_cache: Dict[str, tuple[List['TaskOption'], datetime]] = {}
        self.cache_duration = datetime.timedelta(minutes=5)
        logger.info("PersonalizedTaskGenerator initialized successfully")

    async def generate_task_options(self, user_id: str) -> List['TaskOption']:
        """Generate personalized task options with caching."""
        try:
            # Check cache
            if user_id in self._options_cache:
                options, timestamp = self._options_cache[user_id]
                if datetime.now() - timestamp < self.cache_duration:
                    return options

            # Get user preferences
            prefs = self.preferences_manager.get_user_preferences(user_id)

            # Generate base options
            options = await self._generate_base_options()

            if prefs and prefs.preferred_categories:
                # Filter and sort options based on user preferences
                preferred_options = [
                    opt for opt in options
                    if any(cat.lower() in opt.title.lower() for cat in prefs.preferred_categories)
                ]
                other_options = [
                    opt for opt in options
                    if not any(cat.lower() in opt.title.lower() for cat in prefs.preferred_categories)
                ]

                # Sort preferred options by difficulty if user has a preference
                if prefs.preferred_difficulty:
                    preferred_options.sort(
                        key=lambda x: abs(
                            self._difficulty_score(x.difficulty) - 
                            self._difficulty_score(prefs.preferred_difficulty)
                        )
                    )

                # Combine preferred and other options
                personalized_options = (preferred_options + other_options)[:3]

                # Add "More" option if there are additional options
                if len(options) > 3:
                    personalized_options.append(TaskOption(
                        title="More",
                        subtitle="Load additional tasks",
                        action="fetchMoreOptions"
                    ))

                # Update cache
                self._options_cache[user_id] = (personalized_options, datetime.now())
                return personalized_options

            return options[:3]

        except Exception as e:
            logger.error(f"Error generating personalized tasks for user {user_id}: {e}")
            raise

    async def _generate_base_options(self) -> List['TaskOption']:
        """Generate base task options using the task generator."""
        try:
            # Get base options from the task generator
            base_options = await self.task_generator.generate_options()
            
            if not base_options:
                logger.warning("No base options generated by task generator")
                return []

            # Ensure all options have required fields
            validated_options = []
            for option in base_options:
                if self._validate_task_option(option):
                    validated_options.append(option)
                else:
                    logger.warning(f"Invalid task option skipped: {option}")

            return validated_options

        except Exception as e:
            logger.error(f"Error generating base options: {e}")
            raise

    @staticmethod
    def _validate_task_option(option: 'TaskOption') -> bool:
        """Validate that a task option has all required fields."""
        required_fields = ['title', 'subtitle', 'action']
        return all(hasattr(option, field) and getattr(option, field) for field in required_fields)

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

