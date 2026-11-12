"""
Pattern service for managing integration patterns.

This module provides operations for creating, storing, and retrieving
integration patterns learned from successful integrations.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc
from sqlalchemy.orm import selectinload

from app.models.knowledge import Pattern
from app.models.integration import Integration
from app.services.knowledge.vector_service import VectorService
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import LoggerMixin


class PatternService(LoggerMixin):
    """
    Service for managing integration patterns.
    
    Handles pattern creation, storage, retrieval, and matching
    for reusable integration logic.
    """
    
    def __init__(self, vector_service: Optional[VectorService] = None):
        """
        Initialize the pattern service.
        
        Args:
            vector_service: Vector service for semantic operations
        """
        self.vector_service = vector_service or VectorService()
    
    async def create_pattern(
        self,
        db: AsyncSession,
        name: str,
        pattern_type: str,
        pattern_definition: Dict[str, Any],
        description: Optional[str] = None,
        code_template: Optional[str] = None,
        source_system_types: Optional[List[str]] = None,
        target_system_types: Optional[List[str]] = None,
        use_cases: Optional[List[str]] = None,
        learned_from_integration_id: Optional[uuid.UUID] = None,
        **kwargs: Any
    ) -> Pattern:
        """
        Create a new integration pattern.
        
        Args:
            db: Database session
            name: Pattern name
            pattern_type: Type of pattern
            pattern_definition: Pattern definition data
            description: Pattern description
            code_template: Code template for the pattern
            source_system_types: Applicable source system types
            target_system_types: Applicable target system types
            use_cases: Pattern use cases
            learned_from_integration_id: Source integration ID
            **kwargs: Additional pattern properties
            
        Returns:
            Pattern: Created pattern
        """
        try:
            # Create pattern in database
            pattern = Pattern(
                name=name,
                pattern_type=pattern_type,
                pattern_definition=pattern_definition,
                description=description,
                code_template=code_template,
                source_system_types=source_system_types,
                target_system_types=target_system_types,
                use_cases=use_cases,
                learned_from_integration_id=learned_from_integration_id,
                **kwargs
            )
            
            db.add(pattern)
            await db.flush()  # Get the ID
            
            # Generate semantic embedding
            text_for_embedding = self._create_pattern_text(pattern)
            embedding = await self.vector_service.generate_embedding(text_for_embedding)
            
            # Store in vector database
            await self.vector_service.store_pattern_embedding(
                pattern_id=str(pattern.id),
                embedding=embedding,
                metadata={
                    "name": pattern.name,
                    "pattern_type": pattern.pattern_type,
                    "description": pattern.description,
                    "source_system_types": pattern.source_system_types or [],
                    "target_system_types": pattern.target_system_types or [],
                    "use_cases": pattern.use_cases or [],
                    "confidence_score": pattern.confidence_score,
                }
            )
            
            await db.commit()
            
            self.logger.info(
                "Created pattern",
                pattern_id=str(pattern.id),
                name=pattern.name,
                type=pattern.pattern_type
            )
            
            return pattern
            
        except Exception as e:
            await db.rollback()
            raise ValidationError(
                f"Failed to create pattern: {str(e)}",
                context={"name": name, "type": pattern_type}
            )
    
    def _create_pattern_text(self, pattern: Pattern) -> str:
        """
        Create text representation of pattern for embedding.
        
        Args:
            pattern: Pattern object
            
        Returns:
            str: Text representation
        """
        parts = [
            pattern.name,
            pattern.description or "",
            f"Pattern type: {pattern.pattern_type}",
        ]
        
        if pattern.source_system_types:
            parts.append(f"Source systems: {', '.join(pattern.source_system_types)}")
        
        if pattern.target_system_types:
            parts.append(f"Target systems: {', '.join(pattern.target_system_types)}")
        
        if pattern.use_cases:
            parts.append(f"Use cases: {', '.join(pattern.use_cases)}")
        
        return ". ".join(filter(None, parts))
    
    async def get_pattern(self, db: AsyncSession, pattern_id: uuid.UUID) -> Pattern:
        """
        Get a pattern by ID.
        
        Args:
            db: Database session
            pattern_id: Pattern ID
            
        Returns:
            Pattern: The pattern
            
        Raises:
            NotFoundError: If pattern not found
        """
        query = select(Pattern).where(Pattern.id == pattern_id)
        result = await db.execute(query)
        pattern = result.scalar_one_or_none()
        
        if not pattern:
            raise NotFoundError(f"Pattern not found: {pattern_id}", resource_type="pattern")
        
        return pattern
    
    async def search_patterns(
        self,
        db: AsyncSession,
        query: str,
        pattern_type: Optional[str] = None,
        source_system_type: Optional[str] = None,
        target_system_type: Optional[str] = None,
        limit: int = 10,
        min_similarity: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Search patterns using semantic similarity.
        
        Args:
            db: Database session
            query: Search query
            pattern_type: Filter by pattern type
            source_system_type: Filter by source system type
            target_system_type: Filter by target system type
            limit: Maximum results
            min_similarity: Minimum similarity threshold
            
        Returns:
            List[Dict[str, Any]]: Matching patterns with scores
        """
        # Generate query embedding
        query_embedding = await self.vector_service.generate_embedding(query)
        
        # Search vector database
        similar_patterns = await self.vector_service.search_similar_patterns(
            query_embedding=query_embedding,
            limit=limit * 2,  # Get more to allow for filtering
            min_score=min_similarity,
            pattern_type=pattern_type
        )
        
        if not similar_patterns:
            return []
        
        # Get pattern IDs
        pattern_ids = [uuid.UUID(result["pattern_id"]) for result in similar_patterns]
        
        # Fetch full patterns from database
        query_stmt = select(Pattern).where(Pattern.id.in_(pattern_ids))
        
        if source_system_type:
            query_stmt = query_stmt.where(
                Pattern.source_system_types.contains([source_system_type])
            )
        
        if target_system_type:
            query_stmt = query_stmt.where(
                Pattern.target_system_types.contains([target_system_type])
            )
        
        result = await db.execute(query_stmt)
        patterns = result.scalars().all()
        
        # Combine with similarity scores
        pattern_scores = {result["pattern_id"]: result for result in similar_patterns}
        results = []
        
        for pattern in patterns:
            score_data = pattern_scores.get(str(pattern.id))
            if score_data:
                results.append({
                    "pattern": pattern,
                    "similarity_score": score_data["score"],
                    "distance": score_data["distance"],
                    "metadata": score_data["metadata"]
                })
        
        # Sort by similarity score
        results.sort(key=lambda x: x["similarity_score"], reverse=True)
        
        return results[:limit]
    
    async def find_applicable_patterns(
        self,
        db: AsyncSession,
        source_system_type: str,
        target_system_type: str,
        integration_description: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find patterns applicable to specific system types.
        
        Args:
            db: Database session
            source_system_type: Source system type
            target_system_type: Target system type
            integration_description: Optional description for semantic matching
            limit: Maximum results
            
        Returns:
            List[Dict[str, Any]]: Applicable patterns with applicability scores
        """
        # Base query for applicable patterns
        query = select(Pattern).where(
            (Pattern.source_system_types.is_(None)) |
            (Pattern.source_system_types.contains([source_system_type]))
        ).where(
            (Pattern.target_system_types.is_(None)) |
            (Pattern.target_system_types.contains([target_system_type]))
        ).order_by(desc(Pattern.success_rate), desc(Pattern.usage_count))
        
        result = await db.execute(query)
        applicable_patterns = result.scalars().all()
        
        if not applicable_patterns:
            return []
        
        results = []
        
        # If we have a description, use semantic similarity
        if integration_description:
            query_embedding = await self.vector_service.generate_embedding(
                integration_description
            )
            
            # Get similarity scores for applicable patterns
            pattern_ids = [str(p.id) for p in applicable_patterns]
            
            for pattern in applicable_patterns:
                # Calculate applicability score
                applicability_score = self._calculate_applicability_score(
                    pattern, source_system_type, target_system_type
                )
                
                # Get semantic similarity if we have description
                semantic_score = 0.0
                if integration_description:
                    pattern_text = self._create_pattern_text(pattern)
                    pattern_embedding = await self.vector_service.generate_embedding(pattern_text)
                    
                    # Calculate cosine similarity
                    import numpy as np
                    similarity = np.dot(query_embedding, pattern_embedding) / (
                        np.linalg.norm(query_embedding) * np.linalg.norm(pattern_embedding)
                    )
                    semantic_score = float(similarity)
                
                # Combined score
                combined_score = (applicability_score * 0.6) + (semantic_score * 0.4)
                
                results.append({
                    "pattern": pattern,
                    "applicability_score": applicability_score,
                    "semantic_score": semantic_score,
                    "combined_score": combined_score,
                    "success_rate": pattern.success_rate,
                    "usage_count": pattern.usage_count
                })
        else:
            # Just use applicability scores
            for pattern in applicable_patterns:
                applicability_score = self._calculate_applicability_score(
                    pattern, source_system_type, target_system_type
                )
                
                results.append({
                    "pattern": pattern,
                    "applicability_score": applicability_score,
                    "semantic_score": 0.0,
                    "combined_score": applicability_score,
                    "success_rate": pattern.success_rate,
                    "usage_count": pattern.usage_count
                })
        
        # Sort by combined score
        results.sort(key=lambda x: x["combined_score"], reverse=True)
        
        return results[:limit]
    
    def _calculate_applicability_score(
        self,
        pattern: Pattern,
        source_system_type: str,
        target_system_type: str
    ) -> float:
        """
        Calculate how applicable a pattern is to given system types.
        
        Args:
            pattern: Pattern to evaluate
            source_system_type: Source system type
            target_system_type: Target system type
            
        Returns:
            float: Applicability score (0.0 to 1.0)
        """
        score = 0.0
        
        # Base score from pattern quality
        score += pattern.confidence_score * 0.3
        
        # Success rate contribution
        if pattern.usage_count > 0:
            score += pattern.success_rate * 0.3
        
        # System type matching
        source_match = (
            not pattern.source_system_types or
            source_system_type in pattern.source_system_types
        )
        target_match = (
            not pattern.target_system_types or
            target_system_type in pattern.target_system_types
        )
        
        if source_match and target_match:
            score += 0.4
        elif source_match or target_match:
            score += 0.2
        
        return min(score, 1.0)
    
    async def record_pattern_usage(
        self,
        db: AsyncSession,
        pattern_id: uuid.UUID,
        success: bool = True
    ) -> None:
        """
        Record pattern usage statistics.
        
        Args:
            db: Database session
            pattern_id: Pattern ID
            success: Whether the usage was successful
        """
        pattern = await self.get_pattern(db, pattern_id)
        
        pattern.usage_count += 1
        if success:
            pattern.success_count += 1
        
        await db.commit()
        
        self.logger.info(
            f"Recorded pattern usage: {pattern_id}",
            success=success,
            usage_count=pattern.usage_count,
            success_rate=pattern.success_rate
        )
    
    async def learn_pattern_from_integration(
        self,
        db: AsyncSession,
        integration: Integration,
        pattern_name: Optional[str] = None
    ) -> Optional[Pattern]:
        """
        Learn a new pattern from a successful integration.
        
        Args:
            db: Database session
            integration: Source integration
            pattern_name: Optional pattern name
            
        Returns:
            Pattern: Created pattern or None if not suitable
        """
        # Only learn from successful integrations
        if not integration.validation_passed or not integration.test_passed:
            return None
        
        # Extract pattern information
        pattern_type = integration.integration_type.value
        pattern_name = pattern_name or f"Pattern from {integration.name}"
        
        # Create pattern definition from integration
        pattern_definition = {
            "integration_type": integration.integration_type.value,
            "natural_language_spec": integration.natural_language_spec,
            "generated_code": integration.generated_code,
            "validation_results": integration.validation_results,
            "test_results": integration.test_results,
            "ai_model_used": integration.ai_model_used,
            "processing_time_seconds": integration.processing_time_seconds,
        }
        
        # Determine system types from the integration's source/target connections
        source_system_types = await self._system_types_for(db, integration.source_system_id)
        target_system_types = await self._system_types_for(db, integration.target_system_id)

        # Create the pattern
        pattern = await self.create_pattern(
            db=db,
            name=pattern_name,
            pattern_type=pattern_type,
            pattern_definition=pattern_definition,
            description=f"Pattern learned from integration: {integration.name}",
            code_template=integration.generated_code,
            source_system_types=source_system_types,
            target_system_types=target_system_types,
            learned_from_integration_id=integration.id,
            confidence_score=0.8,  # Initial confidence for learned patterns
        )
        
        self.logger.info(
            f"Learned pattern from integration",
            pattern_id=str(pattern.id),
            integration_id=str(integration.id)
        )
        
        return pattern
    
    async def _system_types_for(
        self,
        db: AsyncSession,
        system_id: Optional[uuid.UUID],
    ) -> List[str]:
        """Return ``[system_type]`` for a system connection id, or ``[]`` if unknown."""
        if system_id is None:
            return []
        # Imported lazily to keep module import side effects unchanged.
        from app.models.system import SystemConnection

        connection = await db.get(SystemConnection, system_id)
        if connection is None:
            self.logger.warning(
                "System connection not found while learning pattern",
                system_id=str(system_id),
            )
            return []
        system_type = connection.system_type
        return [system_type.value if hasattr(system_type, "value") else str(system_type)]

    async def get_pattern_statistics(self, db: AsyncSession) -> Dict[str, Any]:
        """
        Get pattern usage statistics.
        
        Args:
            db: Database session
            
        Returns:
            Dict[str, Any]: Pattern statistics
        """
        # Total patterns
        total_query = select(Pattern)
        total_result = await db.execute(total_query)
        total_patterns = len(total_result.scalars().all())
        
        # Top patterns by usage
        top_patterns_query = (
            select(Pattern)
            .order_by(desc(Pattern.usage_count))
            .limit(10)
        )
        top_result = await db.execute(top_patterns_query)
        top_patterns = top_result.scalars().all()
        
        # Pattern types distribution
        from sqlalchemy import func
        types_query = (
            select(Pattern.pattern_type, func.count(Pattern.id))
            .group_by(Pattern.pattern_type)
            .order_by(desc(func.count(Pattern.id)))
        )
        types_result = await db.execute(types_query)
        pattern_types = [
            {"type": row[0], "count": row[1]}
            for row in types_result.all()
        ]
        
        return {
            "total_patterns": total_patterns,
            "pattern_types": pattern_types,
            "top_patterns": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "type": p.pattern_type,
                    "usage_count": p.usage_count,
                    "success_rate": p.success_rate,
                }
                for p in top_patterns
            ],
        }
