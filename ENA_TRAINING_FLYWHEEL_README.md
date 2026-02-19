# ENA Training Flywheel - Decentralized Training & Evaluation

A production-grade decentralized training and evaluation system for ENA (Animica LLM) that integrates with AICF credits. Enables CPU-first contributions with economic incentives and verifiable work.

## Overview

The ENA Training Flywheel is a comprehensive system for decentralized model improvement through:

- **Artifact System**: Content-addressed manifests for datasets, evals, models, and more
- **Evaluation Framework**: CPU-friendly deterministic grading with 9 evaluation categories
- **Credit Economics**: Quality-based rewards with anti-sybil measures
- **AICF Integration**: Job submission and credit claiming tied to on-chain proofs

### Design Principles

1. **CPU-Friendly**: All core operations run efficiently on standard hardware
2. **Verifiable**: Content-addressed artifacts with deterministic verification
3. **Economic**: Quality bonuses, spam penalties, and diminishing returns
4. **Incremental**: Does not break existing chain/node behavior

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ENA Training Flywheel                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  Artifacts   │  │  Evaluation  │  │   Credits    │    │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤    │
│  │ Manifests    │  │ Suites       │  │ Calculator   │    │
│  │ Verification │  │ Graders      │  │ Bonuses      │    │
│  │ Hashing      │  │ Runners      │  │ Penalties    │    │
│  │ Provenance   │  │ Scoring      │  │ Claims       │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                    AICF Integration                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ Job Types    │  │ Submissions  │  │ Proofs       │    │
│  │ 9 new types  │  │ Queue        │  │ On-chain     │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Job Types

### CPU-Friendly (High Value, Low Compute)

| Job Type | Base Credits | Description |
|----------|--------------|-------------|
| `DATA_CURATION` | 100 | Dataset cleaning, deduplication, quality scoring |
| `EVAL_RUN` | 150 | Model evaluation on test suites |
| `REWARD_MODEL_LABELING` | 120 | Preference pair generation, reward scoring |
| `DISTILLATION_CPU` | 200 | Small student model training from teacher outputs |
| `RAG_INDEX_BUILD` | 100 | Build ANN indices, embeddings caches |
| `POLICY_TEST` | 130 | Safety/refusal/jailbreak testing |

### GPU-Optional (Compute Heavy)

| Job Type | Base Credits | Description |
|----------|--------------|-------------|
| `SFT_TRAIN` | 500 | Supervised fine-tuning |
| `DPO_TRAIN` | 450 | Direct Preference Optimization |
| `PPO_RLHF` | 600 | PPO-based RLHF (optional) |

## Artifact System

### Manifest Types

All artifacts are content-addressed with SHA3-256 hashing:

#### 1. Dataset Manifest
```json
{
  "artifact_id": "ce6082527...",
  "type": "dataset_shard",
  "created_by": "worker_address",
  "source": "repo:/path/to/data",
  "shard_index": 0,
  "total_shards": 10,
  "num_samples": 1000,
  "dedup_method": "minhash",
  "safety_filtered": true,
  "data_hash": "abc123...",
  "created_at": "2024-01-01T00:00:00Z",
  "inputs": [],
  "metrics": {},
  "signatures": []
}
```

#### 2. Eval Report Manifest
```json
{
  "artifact_id": "eval_report_hash...",
  "type": "eval_report",
  "created_by": "worker_address",
  "model_hash": "model_abc...",
  "eval_suite": "ena_v1",
  "total_score": 85.5,
  "category_scores": {
    "code_reasoning": 90.0,
    "blockchain_protocol": 88.0,
    "security": 82.0
  },
  "num_tasks": 100,
  "pass_rate": 0.855,
  "report_hash": "full_report_hash..."
}
```

#### 3. Model Checkpoint Manifest
```json
{
  "artifact_id": "checkpoint_hash...",
  "type": "model_checkpoint",
  "created_by": "worker_address",
  "model_name": "ena-small-v1",
  "base_model": "gpt2",
  "training_method": "SFT",
  "checkpoint_hash": "weights_hash...",
  "num_parameters": 124000000,
  "is_delta": false
}
```

### Verification

Artifacts undergo multi-layered verification:

1. **Hash Verification**: `artifact_id` matches content hash
2. **Schema Validation**: Required fields present and valid
3. **Sample Checking**: Random spot-checks of N samples
4. **Provenance Validation**: Input artifact hashes verified

```python
from ena.artifacts import ArtifactVerifier, DatasetManifest

verifier = ArtifactVerifier(sample_size=10)
result = verifier.verify(manifest, data=dataset, check_provenance=True)

if result.is_valid:
    print(f"✓ Verified {result.artifact_id}")
    print(f"  Samples checked: {result.samples_checked}")
else:
    print(f"✗ Verification failed: {result.message}")
```

### CLI Commands

```bash
# Verify artifact manifest
animica ena artifact verify manifest.json --data dataset.json --samples 20

# Inspect artifact details
animica ena artifact inspect manifest.json

# Compute artifact hash
animica ena artifact hash manifest.json --hash expected_hash
```

## Evaluation System

### Categories

Nine evaluation categories with weighted scoring:

1. **Code Reasoning**: Repo navigation, bug fixes, refactoring
2. **Blockchain Protocol**: Consensus, mempool, RPC correctness
3. **Wallet User Flows**: Tx sending, balances, edge cases
4. **Security**: Threat modeling, exploit detection, safe patching
5. **Math & Logic**: Computational reasoning, problem solving
6. **Tool Use**: CLI usage correctness, command execution
7. **Long Context**: Summarization and operations on large specs
8. **Instruction Following**: Strict formatting constraints
9. **Safety Policy**: Jailbreak resistance, safe behavior

### Graders

CPU-friendly deterministic graders:

#### Exact Match
```python
from ena.evals import ExactMatchGrader

grader = ExactMatchGrader({"ignore_case": True, "ignore_whitespace": True})
passed, score, message = grader.grade(output="Hello", expected="hello")
# passed=True, score=1.0
```

#### Regex
```python
from ena.evals import RegexGrader

grader = RegexGrader({"ignore_case": False})
passed, score, message = grader.grade(
    output="The answer is 42",
    expected=r"answer is \d+"
)
# passed=True, score=1.0
```

#### Code Test
```python
from ena.evals import CodeTestGrader

grader = CodeTestGrader()
passed, score, message = grader.grade(
    output=42,
    expected={
        "test_function": "def test(output): return output == 42",
        "description": "Should return 42"
    }
)
# passed=True, score=1.0
```

#### Schema Validation
```python
from ena.evals import SchemaGrader

grader = SchemaGrader()
passed, score, message = grader.grade(
    output={"key1": "val1", "key2": "val2"},
    expected={
        "type": "dict",
        "required_keys": ["key1", "key2"]
    }
)
# passed=True, score=1.0
```

### Running Evaluations

```python
from ena.evals import EvalSuite, EvalTask, EvalRunner, EvalCategory

# Create suite
suite = EvalSuite(name="ena_v1", version="1.0")

# Add tasks
suite.add_task(EvalTask(
    task_id="code_1",
    category=EvalCategory.CODE_REASONING,
    prompt="Fix the bug in this function: def add(a, b): return a - b",
    expected="def add(a, b): return a + b",
    grader_type="code_test",
    weight=1.0
))

suite.add_task(EvalTask(
    task_id="math_1",
    category=EvalCategory.MATH_LOGIC,
    prompt="What is 2+2?",
    expected="4",
    grader_type="exact_match"
))

# Define model function
def my_model(prompt: str) -> str:
    # Your model inference here
    return model.generate(prompt)

# Run evaluation
runner = EvalRunner(suite, my_model, model_hash="model_abc123")
result = runner.run()

print(f"Total Score: {result.total_score:.1f}/100")
print(f"Pass Rate: {result.pass_rate*100:.1f}%")
print(f"Category Scores: {result.category_scores}")
```

## Credit System

### Credit Calculation

Credits are calculated deterministically:

```
Credits = base + quality_bonus - penalty
```

With diminishing returns:
- Threshold: 1000 credits/day per worker
- Maximum: 2000 credits/day per worker

### Quality Bonuses

| Bonus Type | Amount | Condition |
|------------|--------|-----------|
| Verification Passed | +20 | Artifact passes all checks |
| Eval Improvement | +100 (scaled) | Model improves eval scores |
| Reproducibility | +30 | Work is reproducible |
| First in Category | +50 | First submission in rare category |

### Penalties

| Penalty Type | Amount | Condition |
|--------------|--------|-----------|
| Low Quality | -30 | Quality score < 0.5 |
| Spam | -100 | Detected as spam |
| Duplicate | -80 | Duplicate submission |

### Usage

```python
from ena.credits import CreditCalculator

calc = CreditCalculator()

# Calculate credits for a submission
result = calc.calculate(
    job_type="DATA_CURATION",
    worker_id="worker_abc",
    verification_passed=True,
    eval_improvement=0.05,  # 5% improvement
    is_reproducible=True,
    quality_score=0.9
)

print(f"Base: {result.base_credits}")
print(f"Bonuses: {result.quality_bonus}")
print(f"Penalties: {result.penalty}")
print(f"Total: {result.total_credits}")
print(f"Applied bonuses: {result.bonuses_applied}")
print(f"Reason: {result.reason}")
```

### Claiming Credits

```python
from ena.credits import ClaimManager

manager = ClaimManager()

# Add earned credits
manager.add_credits("worker1", 100)
manager.add_credits("worker1", 150)

# Check balance
balance = manager.get_balance("worker1")  # 250

# Full claim
result = manager.claim("worker1")
# result.amount_claimed = 250, result.remaining_balance = 0

# Partial claim
manager.add_credits("worker1", 300)
result = manager.claim("worker1", amount=100)
# result.amount_claimed = 100, result.remaining_balance = 200
```

## CPU Mining Workflow

### For CPU Contributors

1. **Choose a Job Type**
   - Pick CPU-friendly jobs: DATA_CURATION, EVAL_RUN, etc.
   - Check current rare categories for bonuses

2. **Do the Work**
   ```bash
   # Example: Build dataset from repo
   animica ena dataset build --source repo --path ./ --out da:<id>
   
   # Example: Run evaluation
   animica ena eval run --model <hash> --suite ena_v1 --json
   ```

3. **Create Artifact Manifest**
   ```python
   from ena.artifacts import DatasetManifest, hash_artifact
   
   manifest = DatasetManifest(
       artifact_id="",
       type="dataset_shard",
       created_by="your_address",
       source="repo:/path",
       num_samples=1000,
       # ... other fields
   )
   manifest.artifact_id = hash_artifact(manifest)
   ```

4. **Verify Before Submission**
   ```bash
   animica ena artifact verify manifest.json --data data.json
   ```

5. **Submit to AICF**
   ```bash
   animica aicf jobs submit \
     --plan ena_dataset_build \
     --budget 150 \
     --param manifest=manifest.json
   ```

6. **Claim Credits**
   ```bash
   animica aicf claimable <your_address>
   animica aicf claim --partial 500
   ```

## Development

### Running Tests

```bash
# All ENA tests
pytest ena/tests/ -v

# Specific test modules
pytest ena/tests/test_artifacts.py -v
pytest ena/tests/test_evals.py -v
pytest ena/tests/test_credits.py -v

# With coverage
pytest --cov=ena ena/tests/
```

### Test Summary

- **Artifacts**: 21 tests ✓ (hashing, verification, manifests)
- **Evaluation**: 10 tests ✓ (graders, suites, runners)
- **Credits**: 17 tests ✓ (calculation, bonuses, claims)

**Total: 48 tests passing**

## Implementation Status

### ✅ Completed (Phase 1)

- [x] Job type extensions (9 new training-specific types)
- [x] Artifact manifest system (5 types with verification)
- [x] Content-addressed hashing (SHA3-256)
- [x] Artifact verification (hash, schema, samples, provenance)
- [x] Artifact CLI commands (verify, inspect, hash)
- [x] Evaluation suite structure (9 categories)
- [x] Deterministic graders (exact, regex, code, schema)
- [x] Eval runner with scoring
- [x] Credit calculator with bonuses/penalties
- [x] Diminishing returns per worker/day
- [x] Claim manager (partial/full claims)
- [x] Comprehensive test suite (48 tests)

### 🔄 In Progress (Phase 2)

- [ ] Eval CLI commands (list, run, report)
- [ ] Dataset builders (repo, chain, issues sources)
- [ ] Data pipeline stages (ingest, dedupe, filter, shard)
- [ ] Model registry with governance
- [ ] AICF job submission integration
- [ ] Credits CLI (status, claim)

### 📋 Planned (Phase 3)

- [ ] Distillation workers
- [ ] Safety policy gates
- [ ] Monitoring metrics
- [ ] Production hardening
- [ ] End-to-end integration tests
- [ ] Deployment documentation

## Security Considerations

### Anti-Sybil Measures

1. **Diminishing Returns**: Credits reduce after 1000/day threshold
2. **Daily Limits**: Maximum 2000 credits per worker per day
3. **Spam Detection**: Heavy penalties for spam submissions
4. **Duplicate Detection**: Penalties for duplicate work

### Verification

1. **Deterministic Hashing**: Content-addressed artifacts
2. **Sample Checking**: Random spot-checks prevent cheating
3. **Provenance Tracking**: Input artifact validation
4. **Schema Enforcement**: Strict validation of all fields

### Privacy

- All payloads are opaque to AICF
- Use redacted digests or DA references for sensitive data
- Worker identities are addresses (no PII required)

## FAQ

**Q: Can I contribute with only a CPU?**  
A: Yes! DATA_CURATION, EVAL_RUN, REWARD_MODEL_LABELING, DISTILLATION_CPU, RAG_INDEX_BUILD, and POLICY_TEST are all CPU-friendly.

**Q: How are credits converted to ANM?**  
A: Credits can be claimed as ANM through the AICF claim mechanism, or used to fund future compute jobs.

**Q: What prevents spam?**  
A: Spam penalties (-100 credits), diminishing returns, daily limits (2000/day), and verification requirements.

**Q: How do I get bonus credits?**  
A: Pass verification (+20), improve eval scores (up to +100), ensure reproducibility (+30), or be first in a rare category (+50).

**Q: Can I partially claim credits?**  
A: Yes! Use `animica aicf claim --partial <amount>` to claim a specific amount.

**Q: How does the eval system work?**  
A: Create tasks in a suite, choose graders (exact/regex/code/schema), run with your model, get scores by category and overall.

## License

See LICENSE.txt in the repository root.

## Contributing

Contributions welcome! Please:
1. Follow existing code style
2. Add tests for new features
3. Update documentation
4. Run full test suite before submitting

## Support

- GitHub Issues: For bugs and feature requests
- Documentation: This README and inline code comments
- Tests: Executable examples in `ena/tests/`
