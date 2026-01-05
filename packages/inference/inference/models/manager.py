"""
Model Manager for Loading and Managing LLM Models
"""

from typing import Optional, Dict
import logging
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
import torch

from inference.config import settings

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages loading and inference for LLM models"""
    
    def __init__(self):
        self.models: Dict[str, any] = {}
        self.tokenizers: Dict[str, any] = {}
        self.pipelines: Dict[str, any] = {}
        
    def load_model(self, model_name: str) -> bool:
        """Load a model into memory"""
        
        if model_name in self.models:
            logger.info(f"Model {model_name} already loaded")
            return True
        
        try:
            logger.info(f"Loading model: {model_name}")
            
            # Use CPU for development
            device = "cuda" if settings.GPU_ENABLED and torch.cuda.is_available() else "cpu"
            
            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                cache_dir=settings.MODEL_CACHE_DIR
            )
            
            # Load model
            model = AutoModelForCausalLM.from_pretrained(
                model_name,
                cache_dir=settings.MODEL_CACHE_DIR,
                device_map=device if device == "cuda" else None,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            )
            
            if device == "cpu":
                model = model.to(device)
            
            # Create pipeline
            pipe = pipeline(
                "text-generation",
                model=model,
                tokenizer=tokenizer,
                device=0 if device == "cuda" else -1,
            )
            
            self.models[model_name] = model
            self.tokenizers[model_name] = tokenizer
            self.pipelines[model_name] = pipe
            
            logger.info(f"Model {model_name} loaded successfully on {device}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load model {model_name}: {e}")
            return False
    
    def generate(
        self,
        model_name: str,
        prompt: str,
        max_tokens: int = 100,
        temperature: float = 0.7,
        top_p: float = 0.9,
        **kwargs
    ) -> str:
        """Generate text from prompt"""
        
        # Ensure model is loaded
        if model_name not in self.pipelines:
            if not self.load_model(model_name):
                raise ValueError(f"Failed to load model: {model_name}")
        
        pipe = self.pipelines[model_name]
        
        try:
            result = pipe(
                prompt,
                max_new_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                do_sample=temperature > 0,
                pad_token_id=self.tokenizers[model_name].eos_token_id,
                **kwargs
            )
            
            generated_text = result[0]["generated_text"]
            
            # Extract only the new generation (remove prompt)
            if generated_text.startswith(prompt):
                generated_text = generated_text[len(prompt):]
            
            return generated_text.strip()
            
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            raise
    
    def count_tokens(self, model_name: str, text: str) -> int:
        """Count tokens in text"""
        
        if model_name not in self.tokenizers:
            if not self.load_model(model_name):
                raise ValueError(f"Failed to load model: {model_name}")
        
        tokenizer = self.tokenizers[model_name]
        tokens = tokenizer.encode(text)
        return len(tokens)
    
    def list_loaded_models(self) -> list:
        """List currently loaded models"""
        return list(self.models.keys())


# Global model manager instance
model_manager = ModelManager()
