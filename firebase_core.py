"""
Firebase Core Integration for Sisyphus Dividend
Architectural Principle: Centralized data layer with atomic operations and real-time synchronization
"""

import firebase_admin
from firebase_admin import credentials, firestore, auth
from google.cloud.firestore import Client, Transaction
from typing import Dict, Any, Optional, List, Tuple
import logging
import hashlib
import json
from datetime import datetime, UTC
from dataclasses import asdict
import threading

class FirebaseManager:
    """Singleton manager for Firebase operations"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FirebaseManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.logger = logging.getLogger(__name__)
        self.db: Optional[Client] = None
        self.initialized = False
        self._initialized = True
    
    def initialize(self, service_account_path: str, project_id: str) -> bool:
        """Initialize Firebase connection"""
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(service_account_path)
                firebase_admin.initialize_app(cred, {
                    'projectId': project_id,
                })
            
            self.db = firestore.client()
            self.initialized = True
            self.logger.info(f"Firebase initialized for project {project_id}")
            
            # Test connection
            test_doc = self.db.collection('system_health').document('connection_test')
            test_doc.set({
                'timestamp': datetime.now(UTC).isoformat(),
                'status': 'active',
                'version': '1.0.0'
            }, merge=True)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Firebase initialization failed: {e}")
            self.initialized = False
            return False
    
    def create_contribution(self, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Create a new contribution with atomic validation
        
        Args:
            data: Contribution data including worker_id, task_id, result, quality_score
            
        Returns:
            Tuple of (success, contribution_id or error_message)
        """
        if not self.initialized or not self.db:
            return False, "Firebase not initialized"
        
        try:
            # Generate deterministic ID from content
            content_hash = hashlib.sha256(
                f"{data.get('worker_id')}{data.get('task_id')}{datetime.now(UTC).isoformat()}".encode()
            ).hexdigest()[:20]
            
            contribution_id = f"cont_{content_hash}"
            
            # Prepare contribution document
            contribution_data = {
                'id': contribution_id,
                'created_at': datetime.now(UTC).isoformat(),
                'worker_id': data['worker_id'],
                'task_id': data['task_id'],
                'platform': data.get('platform', 'unknown'),
                'result': data['result'],
                'quality_score': data.get('quality_score', 0.5),
                'value_generated': data.get('value_generated', 0.0),
                'status': 'pending_validation',
                'metadata': data.get('metadata', {}),
                'hash_chain': self._calculate_hash_chain(data)
            }
            
            # Use transaction for atomic write
            @firestore.transactional
            def create_in_transaction(transaction: Transaction, doc_ref):
                # Check if contribution already exists
                snapshot = doc_ref.get(transaction=transaction)
                if snapshot.exists:
                    return False, "Contribution already exists"
                
                # Create contribution
                transaction.set(doc_ref, contribution_data)
                
                # Update worker quality score
                worker_ref = self.db.collection('quality_scores').document(data['worker_id'])
                worker_snapshot = worker_ref.get(transaction=transaction)
                
                if worker_snapshot.exists:
                    current_score = worker_snapshot.get('score', 0.5)
                    count = worker_snapshot.get('count', 0)
                    new_score = (current_score * count + data.get('quality_score', 0.5)) / (count + 1)
                    
                    transaction.update(worker_ref, {
                        'score': new_score,
                        'count': count + 1,
                        'last_contribution': datetime.now(UTC).isoformat(),
                        'updated_at': datetime.now(UTC).isoformat()
                    })
                else:
                    transaction.set(worker_ref, {
                        'worker_id': data['worker_id'],
                        'score': data.get('quality_score', 0.5),
                        'count': 1,
                        'created_at': datetime.now(UTC).isoformat(),
                        'last_contribution': datetime.now(UTC).isoformat()
                    })
                
                return True, contribution_id
            
            transaction = self.db.transaction()
            doc_ref = self.db.collection('contributions').document(contribution_id)
            success, message = create_in_transaction(transaction, doc_ref)
            
            if success:
                self.logger.info(f"Created contribution {contribution_id}")
                return True, contribution_id
            else:
                return False, message
            
        except Exception as e:
            self.logger.error(f"Failed to create contribution: {e}")
            return False, str(e)
    
    def create_asset(self, asset_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Create a new intelligence asset from contributions
        
        Args:
            asset_data: Asset metadata including contribution_ids, asset_type, description
            
        Returns:
            Tuple of (success, asset_id)
        """
        try:
            asset_id = f"asset_{hashlib.sha256(asset_data['name'].encode()).hexdigest()[:16]}"
            
            asset_doc = {
                'id': asset_id,
                'name': asset_data['name'],
                'description': asset_data.get('description', ''),
                'asset_type': asset_data['asset_type'],
                'contribution_ids': asset_data['contribution_ids'],
                'contribution_count': len(asset_data['contribution_ids']),
                'total_value': asset_data.get('total_value', 0.0),
                'created_at': datetime.now(UTC).isoformat(),
                'status': 'training',
                'metadata': asset_data.get('metadata', {}),
                'owner_distribution': {}  # To be populated when asset generates revenue
            }
            
            self.db.collection('assets').document(asset_id).set(asset_doc)
            self.logger.info(f"Created asset {asset_id} with {len(asset_data['contribution_ids'])} contributions")
            
            return True, asset_id
            
        except Exception as e:
            self.logger.error(f"Failed to create asset: {e}")
            return False, None
    
    def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending tasks for workers"""
        try:
            tasks_ref = self.db.collection('task_queue')\
                .where('status', '==', 'pending')\
                .order_by('priority')\
                .limit(limit)
            
            tasks = []
            for doc in tasks_ref.stream():
                task_data = doc.to_dict()
                task_data['doc_id'] = doc.id
                tasks.append(task_data)
            
            return tasks
            
        except Exception as e:
            self.logger.error(f"Failed to get pending tasks: {e}")
            return []
    
    def update_task_status(self, task_id: str, status: str, result: Optional[Dict] = None) -> bool:
        """Update task status with result"""
        try:
            update_data = {
                'status': status,
                'updated_at': datetime.now(UTC).isoformat()
            }
            
            if result:
                update_data['result'] = result
                update_data['completed_at'] = datetime.now(UTC).isoformat()
            
            self.db.collection('task_queue').document(task_id).update(update_data)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update task {task_id}: {e}")
            return False
    
    def _calculate_hash_chain(self, data: Dict[str, Any]) -> str:
        """Calculate hash chain for contribution immutability"""
        chain_data = json.dumps(data, sort_keys=True).encode()
        return hashlib.sha256(chain_data).hexdigest()
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get system health metrics"""
        try:
            health_data = {
                'firebase_connected': self.initialized,
                'timestamp': datetime.now(UTC).isoformat(),
                'collections': {}
            }
            
            if self.initialized:
                collections = ['contributions', 'assets', 'quality_scores', 'task_queue']
                for collection in collections:
                    try:
                        count = len(list(self.db.collection(collection).limit(1000).stream()))
                        health_data['collections'][collection] = {
                            'count': count,
                            'status': 'active'
                        }
                    except Exception as e:
                        health_data['collections'][collection] = {
                            'count': 0,
                            'status': f'error: {str(e)}'
                        }
            
            return health_data
            
        except Exception as e:
            self.logger.error(f"Failed to get system health: {e}")
            return {'error': str(e)}