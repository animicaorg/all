# Training

Training is represented as `fine_tuning_training` async jobs:

- Dataset URI and hyperparameters in job input payload.
- Scheduler routes to nodes with `training` runtime capability.
- Settlement and payouts use same escrow/reward pipeline as inference jobs.
- Training adapter currently scaffolded for checkpoint + metric output.
