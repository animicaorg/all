from enum import Enum


class JobKind(str, Enum):
    """
    Job type classifications for AICF work submissions.
    
    Original types (backward compatible):
    - AI: General AI inference/compute jobs
    - QUANTUM: Quantum computation jobs
    
    ENA Training Flywheel types (CPU-friendly, high value):
    - DATA_CURATION: Dataset cleaning, deduplication, quality scoring
    - EVAL_RUN: Model evaluation on test suites
    - REWARD_MODEL_LABELING: Preference pair generation, reward scoring
    - DISTILLATION_CPU: Small student model training from teacher outputs
    - RAG_INDEX_BUILD: Build ANN indices, embeddings caches for RAG
    - POLICY_TEST: Safety/refusal/jailbreak testing
    
    ENA Training Flywheel types (GPU-optional, compute heavy):
    - SFT_TRAIN: Supervised fine-tuning
    - DPO_TRAIN: Direct Preference Optimization training
    - PPO_RLHF: PPO-based RLHF (optional)
    """
    # Original types (backward compatible)
    AI = "AI"
    QUANTUM = "QUANTUM"
    
    # CPU-friendly training jobs
    DATA_CURATION = "DATA_CURATION"
    EVAL_RUN = "EVAL_RUN"
    REWARD_MODEL_LABELING = "REWARD_MODEL_LABELING"
    DISTILLATION_CPU = "DISTILLATION_CPU"
    RAG_INDEX_BUILD = "RAG_INDEX_BUILD"
    POLICY_TEST = "POLICY_TEST"
    
    # GPU-optional training jobs
    SFT_TRAIN = "SFT_TRAIN"
    DPO_TRAIN = "DPO_TRAIN"
    PPO_RLHF = "PPO_RLHF"


def is_cpu_friendly(kind: JobKind) -> bool:
    """Check if job type can run efficiently on CPU-only hardware."""
    return kind in {
        JobKind.DATA_CURATION,
        JobKind.EVAL_RUN,
        JobKind.REWARD_MODEL_LABELING,
        JobKind.DISTILLATION_CPU,
        JobKind.RAG_INDEX_BUILD,
        JobKind.POLICY_TEST,
    }


def is_training_job(kind: JobKind) -> bool:
    """Check if job type is part of ENA training flywheel."""
    return kind in {
        JobKind.DATA_CURATION,
        JobKind.EVAL_RUN,
        JobKind.REWARD_MODEL_LABELING,
        JobKind.DISTILLATION_CPU,
        JobKind.RAG_INDEX_BUILD,
        JobKind.POLICY_TEST,
        JobKind.SFT_TRAIN,
        JobKind.DPO_TRAIN,
        JobKind.PPO_RLHF,
    }
