"""
Simple CPU-only LLM inference engine.

This is a minimal implementation for demonstration purposes.
In production, this would integrate with actual ML models.
"""

import logging
import random
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class InferenceEngine:
    """Simple CPU-only inference engine."""
    
    def __init__(self, model_path: str, model_name: str):
        self.model_path = model_path
        self.model_name = model_name
        self._load_model()
    
    def _load_model(self):
        """Load the model (placeholder for actual model loading)."""
        logger.info(f"Loading model: {self.model_name} from {self.model_path}")
        # In a real implementation, this would load actual model weights
        # For now, we'll use a simple rule-based system
        self.ready = True
    
    def infer(
        self,
        prompt: str,
        max_tokens: int = 100,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Run inference on a prompt.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0 to 1.0)
        
        Returns:
            Dictionary with:
                - answer: Generated text
                - usage: Token usage statistics
        """
        if not self.ready:
            raise RuntimeError("Model not loaded")
        
        # Tokenize (simple word-based for demo)
        prompt_tokens = self._tokenize(prompt)
        
        # Generate response (simple for demo)
        answer = self._generate(prompt, max_tokens)
        completion_tokens = self._tokenize(answer)
        
        return {
            "answer": answer,
            "usage": {
                "promptTokens": len(prompt_tokens),
                "completionTokens": len(completion_tokens),
                "totalTokens": len(prompt_tokens) + len(completion_tokens),
            }
        }
    
    def _tokenize(self, text: str) -> list:
        """Simple tokenization (word-based)."""
        return text.split()
    
    def _generate(self, prompt: str, max_tokens: int) -> str:
        """
        Generate response (placeholder implementation).
        
        In a real implementation, this would use the actual model.
        For demo purposes, we'll use simple rules.
        """
        # Simple echo-based response with variation
        responses = [
            f"I understand you said: '{prompt}'. This is a demo response from the ENA service.",
            f"Received your prompt: '{prompt}'. The ENA system is working correctly.",
            f"Processing: '{prompt}'. This demonstrates the CPU-based inference capability.",
        ]
        
        response = random.choice(responses)
        
        # Truncate to max_tokens (rough approximation)
        tokens = response.split()
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]
            response = " ".join(tokens) + "..."
        
        return response


def create_inference_engine(model_path: str, model_name: str) -> InferenceEngine:
    """Factory function to create an inference engine."""
    return InferenceEngine(model_path, model_name)
