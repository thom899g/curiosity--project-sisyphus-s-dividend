"""
Project Sisyphus - Configuration and Environment Management
Architectural Principle: Centralized configuration with runtime validation
"""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path
import hashlib
import logging

@dataclass
class FirebaseConfig:
    """Firebase configuration with validation"""
    project_id: str = "sisyphus-dividend"
    service_account_path: Path = Path("firebase-service-account.json")
    collections: Dict[str, str] = field(default_factory=lambda: {
        "contributions": "contributions",
        "assets": "assets",
        "ownership": "ownership",
        "quality_scores": "quality_scores",
        "task_graphs": "task_graphs",
        "platform_metrics": "platform_metrics"
    })
    
    def validate(self) -> bool:
        """Validate Firebase configuration"""
        if not self.service_account_path.exists():
            logging.error(f"Firebase service account not found at {self.service_account_path}")
            return False
        return True

@dataclass
class TaskPlatform:
    """Configuration for task platforms"""
    name: str
    api_endpoint: str
    rate_limit_per_hour: int
    min_payout_threshold: float
    priority_score: float = 1.0
    enabled: bool = True
    last_accessed: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "api_endpoint": self.api_endpoint,
            "rate_limit": self.rate_limit_per_hour,
            "min_payout": self.min_payout_threshold,
            "priority": self.priority_score,
            "enabled": self.enabled,
            "last_accessed": self.last_accessed
        }

@dataclass
class BehavioralModelConfig:
    """Mimetic learning configuration"""
    human_timing_mean: float = 8.5  # seconds for micro-task
    human_timing_std: float = 3.2
    min_think_time: float = 1.5
    max_think_time: float = 15.0
    typing_speed_wpm: int = 45
    error_rate: float = 0.05  # 5% error rate
    
    def generate_task_time(self) -> float:
        """Generate realistic task completion time"""
        import numpy as np
        base_time = np.random.normal(self.human_timing_mean, self.human_timing_std)
        return np.clip(base_time, self.min_think_time, self.max_think_time)

class SisyphusConfig:
    """Main configuration manager"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.logger = logging.getLogger(__name__)
        self.firebase = FirebaseConfig()
        self.behavior = BehavioralModelConfig()
        
        # Task platforms (to be populated from Firestore)
        self.platforms: Dict[str, TaskPlatform] = {}
        
        # Performance thresholds
        self.min_yield_per_hour: float = 0.01  # $0.01/hour minimum
        self.max_concurrent_tasks: int = 5
        self.circuit_breaker_threshold: int = 3  # consecutive failures
        
        # Asset parameters
        self.min_contributions_for_asset: int = 1000
        self.asset_revenue_share: float = 0.70  # 70% to contributors
        
        # Initialize from file if provided
        if config_path and os.path.exists(config_path):
            self._load_from_file(config_path)
        else:
            self._load_defaults()
    
    def _load_defaults(self) -> None:
        """Load default platform configurations"""
        default_platforms = [
            TaskPlatform(
                name="microtask_api",
                api_endpoint="https://api.microtask.example/v1",
                rate_limit_per_hour=60,
                min_payout_threshold=0.10
            ),
            TaskPlatform(
                name="data_cleanup_service",
                api_endpoint="https://cleanup.example.com/api",
                rate_limit_per_hour=30,
                min_payout_threshold=0.25
            )
        ]
        
        for platform in default_platforms:
            self.platforms[platform.name] = platform
    
    def _load_from_file(self, config_path: str) -> None:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
                
            if 'firebase' in data:
                self.firebase.project_id = data['firebase'].get('project_id', self.firebase.project_id)
                
            if 'platforms' in data:
                for platform_data in data['platforms']:
                    platform = TaskPlatform(**platform_data)
                    self.platforms[platform.name] = platform
                    
            self.logger.info(f"Loaded configuration from {config_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to load config from {config_path}: {e}")
            self._load_defaults()
    
    def get_platform(self, name: str) -> Optional[TaskPlatform]:
        """Get platform configuration by name"""
        return self.platforms.get(name)
    
    def update_platform_metric(self, platform_name: str, success: bool) -> None:
        """Update platform performance metrics"""
        if platform_name in self.platforms:
            platform = self.platforms[platform_name]
            if not success:
                # Degrade priority on failure
                platform.priority_score = max(0.1, platform.priority_score * 0.8)
            else:
                # Gradually recover priority
                platform.priority_score = min(1.0, platform.priority_score * 1.05)