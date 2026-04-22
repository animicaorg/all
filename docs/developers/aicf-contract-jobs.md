# AICF Contract-Driven Jobs

Smart contracts can fund AI workloads by escrowing ANM and triggering AICF jobs.

## Pattern

1. Contract escrows ANM for a job budget.
2. AICF scheduler dispatches workload to qualified providers.
3. Provider submits execution receipt.
4. Settlement callback finalizes usage and rewards.
5. Optional dispute window allows challenge/slash before closure.

## Job classes

- `chat_inference`
- `embedding_generation`
- `agent_task`
- `fine_tuning_training`
- `custom_compute`
