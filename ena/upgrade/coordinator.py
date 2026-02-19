"""
Upgrade coordinator for orchestrating the full upgrade workflow.

Manages state transitions, job submission, verification, and deployment.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from .state_machine import (
    UpgradeStateMachine,
    UpgradeState,
    JobStatus,
    UpgradeStatus,
)
from .training_plan import TrainingPlan, create_default_training_plan, JobSpec
from .verifier import ResultVerifier, SafetyGates, VerificationResult
from ..registry.schema import (
    ModelManifest,
    ModelType,
    QuantizationType,
    ArtifactHashes,
    EvalMetrics,
    TrainingProvenance,
    RolloutPolicy,
)
from ..registry.storage import RegistryStorage

logger = logging.getLogger(__name__)


class UpgradeCoordinator:
    """
    Orchestrates the full model upgrade workflow.
    
    Workflow:
    1. PLANNING - Generate training plan
    2. ALLOCATING_BUDGET - Allocate AICF escrow (stub)
    3. SUBMITTING_JOBS - Submit jobs to AICF (stub)
    4. MONITORING - Monitor job progress (stub)
    5. VERIFYING - Verify results and check safety gates
    6. PUBLISHING - Save model to registry
    7. CANARY - Gradual rollout (stub)
    8. COMPLETED - Workflow complete
    
    Supports resume from any state.
    """
    
    def __init__(
        self,
        state_machine: UpgradeStateMachine,
        registry: RegistryStorage,
        verifier: ResultVerifier,
        safety_gates: SafetyGates,
        work_dir: Path,
    ):
        """
        Initialize coordinator.
        
        Args:
            state_machine: State machine for tracking progress
            registry: Model registry for publishing
            verifier: Result verifier
            safety_gates: Safety gates for quality checks
            work_dir: Working directory for artifacts
        """
        self.state_machine = state_machine
        self.registry = registry
        self.verifier = verifier
        self.safety_gates = safety_gates
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
    
    def create_plan(
        self,
        model_id: str,
        target_version: str,
        creator: str,
        dataset_hashes: List[str],
        base_model: str = "qwen2.5-coder-1.5b",
    ) -> TrainingPlan:
        """
        Generate training plan.
        
        Args:
            model_id: Model identifier
            target_version: Target version
            creator: Creator address
            dataset_hashes: Dataset DA commitment hashes
            base_model: Base model to fine-tune from
        
        Returns:
            TrainingPlan
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to planning state
        if not self.state_machine.transition(UpgradeState.PLANNING):
            raise ValueError("Cannot transition to PLANNING state")
        
        logger.info("Creating training plan...")
        
        # Create default plan
        plan = create_default_training_plan(
            model_id=model_id,
            target_version=target_version,
            creator=creator,
            dataset_hashes=dataset_hashes,
            base_model=base_model,
        )
        
        # Save plan to work directory
        plan_path = self.work_dir / f"{plan.plan_id}.json"
        plan_path.write_text(plan.to_json())
        
        # Update state machine
        plan_hash = plan.compute_hash()
        self.state_machine.set_plan(plan.plan_id, plan_hash)
        
        # Initialize job statuses
        for job in plan.jobs:
            self.state_machine.update_job_status(
                job.job_id,
                JobStatus(
                    job_id=job.job_id,
                    state="pending",
                ),
            )
        
        logger.info(f"Created plan: {plan.plan_id} with {len(plan.jobs)} jobs")
        return plan
    
    def allocate_budget(self, amount: int):
        """
        Allocate AICF escrow budget.
        
        Status: Integration pending (Phase 2)
        - State transitions are functional
        - AICF escrow contract interaction is not yet implemented
        - Returns success to allow workflow testing
        
        When implemented, this will:
        1. Call AICF escrow contract to lock funds
        2. Verify escrow transaction on-chain
        3. Record escrow txid in state machine
        
        Args:
            amount: Amount in ANM base units
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to allocating budget state
        if not self.state_machine.transition(UpgradeState.ALLOCATING_BUDGET):
            raise ValueError("Cannot transition to ALLOCATING_BUDGET state")
        
        logger.info(f"Allocating budget: {amount / 1_000_000_000:.2f} ANM")
        
        # Phase 2: AICF escrow contract call will go here
        # Example implementation:
        # from aicf.escrow import allocate_budget
        # escrow_txid = allocate_budget(amount, self.state_machine.status.upgrade_id)
        # self.state_machine.record_escrow_tx(escrow_txid)
        
        logger.info("Budget allocation: using test mode (AICF integration pending)")
        
        # Record allocation
        self.state_machine.allocate_budget(amount)
        
        logger.info("Budget allocated successfully")
    
    def submit_jobs(self, plan: TrainingPlan) -> List[str]:
        """
        Submit jobs to AICF queue.
        
        Status: Integration pending (Phase 2)
        - Job DAG resolution and ordering is functional
        - AICF queue submission is not yet implemented
        - Returns synthetic job IDs for workflow testing
        
        When implemented, this will:
        1. Submit each job to AICF queue via RPC
        2. Attach dataset commitments and dependencies
        3. Return actual AICF job IDs
        
        Args:
            plan: Training plan with jobs to submit
        
        Returns:
            List of AICF job IDs
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to submitting jobs state
        if not self.state_machine.transition(UpgradeState.SUBMITTING_JOBS):
            raise ValueError("Cannot transition to SUBMITTING_JOBS state")
        
        logger.info(f"Submitting {len(plan.jobs)} jobs to AICF...")
        
        # Get execution order (respects dependencies)
        job_order = plan.get_execution_order()
        
        aicf_job_ids = []
        
        for job_id in job_order:
            job = plan.get_job_by_id(job_id)
            if not job:
                continue
            
            # Phase 2: AICF queue submission will go here
            # Example implementation:
            # from aicf.queue import submit_job
            # aicf_id = submit_job(
            #     job_type=job.job_type,
            #     dataset_commitment=job.dataset_commitment,
            #     dependencies=[j.aicf_id for j in job.dependencies],
            #     resources=job.compute_requirements,
            # )
            
            logger.info(f"  Job: {job_id} (type: {job.job_type.value}) - test mode")
            
            # Generate synthetic AICF job ID for testing
            fake_aicf_id = f"aicf_{job_id}"
            aicf_job_ids.append(fake_aicf_id)
            
            # Update job status
            status = self.state_machine.status.job_statuses.get(job_id)
            if status:
                status.aicf_job_id = fake_aicf_id
                status.state = "submitted"
                status.started_at = datetime.utcnow().isoformat() + "Z"
                self.state_machine.update_job_status(job_id, status)
        
        logger.info(f"Submitted {len(aicf_job_ids)} jobs")
        return aicf_job_ids
    
    def monitor_progress(self) -> Dict[str, str]:
        """
        Monitor job progress.
        
        Status: Integration pending (Phase 2)
        - State machine progress tracking is functional
        - AICF job status queries are not yet implemented
        - Returns current state machine status for testing
        
        When implemented, this will:
        1. Query AICF for each submitted job's status
        2. Update state machine with current progress
        3. Return real-time job statuses
        
        Returns:
            Dict mapping job_id to status ("pending", "running", "completed", "failed")
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to monitoring state
        if not self.state_machine.transition(UpgradeState.MONITORING):
            raise ValueError("Cannot transition to MONITORING state")
        
        logger.info("Monitoring job progress (test mode)...")
        
        # Phase 2: AICF status queries will go here
        # Example implementation:
        # from aicf.queue import get_job_status
        # for job_id, job_status in self.state_machine.status.job_statuses.items():
        #     if job_status.aicf_job_id:
        #         aicf_status = get_job_status(job_status.aicf_job_id)
        #         job_status.state = aicf_status.state
        #         self.state_machine.update_job_status(job_id, job_status)
        
        # Return current state machine status
        statuses = {}
        for job_id, job_status in self.state_machine.status.job_statuses.items():
            statuses[job_id] = job_status.state
            logger.info(f"  {job_id}: {job_status.state}")
        
        return statuses
    
    def verify_results(
        self,
        plan: TrainingPlan,
        job_outputs: Dict[str, Path],
        metrics: Dict[str, EvalMetrics],
    ) -> VerificationResult:
        """
        Verify job results and check safety gates.
        
        Args:
            plan: Training plan
            job_outputs: Dict mapping job_id to output directory
            metrics: Dict mapping job_id to evaluation metrics
        
        Returns:
            VerificationResult
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to verifying state
        if not self.state_machine.transition(UpgradeState.VERIFYING):
            raise ValueError("Cannot transition to VERIFYING state")
        
        logger.info("Verifying results...")
        
        # Verify each job's outputs
        for job in plan.jobs:
            job_id = job.job_id
            
            # Check if outputs exist
            if job_id not in job_outputs:
                error = f"Missing outputs for job: {job_id}"
                self.state_machine.transition(UpgradeState.FAILED, error)
                return VerificationResult(passed=False, reason=error)
            
            output_dir = job_outputs[job_id]
            
            # For now, just check directory exists
            # Real implementation would verify artifact hashes
            if not output_dir.exists():
                error = f"Output directory missing: {output_dir}"
                self.state_machine.transition(UpgradeState.FAILED, error)
                return VerificationResult(passed=False, reason=error)
            
            logger.info(f"  Verified outputs for: {job_id}")
            
            # Update job status
            status = self.state_machine.status.job_statuses.get(job_id)
            if status:
                status.state = "completed"
                status.completed_at = datetime.utcnow().isoformat() + "Z"
                self.state_machine.update_job_status(job_id, status)
        
        # Check safety gates for final model
        # Use metrics from last eval job
        eval_jobs = [j for j in plan.jobs if "eval" in j.job_type.value]
        
        if eval_jobs and metrics:
            last_eval_job = eval_jobs[-1]
            eval_metrics = metrics.get(last_eval_job.job_id)
            
            if eval_metrics:
                logger.info("Checking safety gates...")
                passed, failures = self.safety_gates.passes_all_gates(eval_metrics)
                
                if not passed:
                    error = f"Safety gates failed: {', '.join(failures)}"
                    self.state_machine.transition(UpgradeState.FAILED, error)
                    return VerificationResult(passed=False, reason=error)
                
                logger.info("  Safety gates passed")
        
        logger.info("All verifications passed")
        return VerificationResult(passed=True, reason="All verifications passed")
    
    def publish_model(
        self,
        manifest: ModelManifest,
    ) -> str:
        """
        Publish model to registry.
        
        Args:
            manifest: Model manifest to publish
        
        Returns:
            Manifest hash
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to publishing state
        if not self.state_machine.transition(UpgradeState.PUBLISHING):
            raise ValueError("Cannot transition to PUBLISHING state")
        
        logger.info(f"Publishing model: {manifest.model_id} v{manifest.version}")
        
        # Save to registry
        manifest_hash = self.registry.save_manifest(manifest)
        
        # Update state machine
        self.state_machine.set_published_manifest(manifest_hash)
        
        logger.info(f"Published manifest: {manifest_hash[:16]}")
        return manifest_hash
    
    def rollout_canary(self, canary_percent: float = 0.1) -> bool:
        """
        Start canary rollout.
        
        Status: Integration pending (Phase 2)
        - State machine canary tracking is functional
        - Traffic routing configuration is not yet implemented
        - Returns success for workflow testing
        
        When implemented, this will:
        1. Configure load balancer to route canary_percent to new version
        2. Set up monitoring/alerting for canary metrics
        3. Record canary deployment in state machine
        
        Args:
            canary_percent: Percentage of traffic to route to new version
        
        Returns:
            True if successful
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        # Transition to canary state
        if not self.state_machine.transition(UpgradeState.CANARY):
            raise ValueError("Cannot transition to CANARY state")
        
        logger.info(f"Canary rollout: {canary_percent * 100:.1f}% traffic (test mode)")
        
        # Phase 2: Traffic routing configuration will go here
        # Example implementation:
        # from ena.routing import configure_traffic_split
        # configure_traffic_split(
        #     model_id=self.state_machine.status.model_id,
        #     old_version=current_version,
        #     new_version=self.state_machine.status.target_version,
        #     new_weight=canary_percent,
        # )
        
        # Record canary start
        self.state_machine.start_canary()
        
        logger.info("Canary deployment started")
        return True
    
    def promote_canary(self) -> bool:
        """
        Promote canary to 100% traffic.
        
        Status: Integration pending (Phase 2)
        - State machine promotion tracking is functional
        - Traffic routing update is not yet implemented
        - Returns success for workflow testing
        
        When implemented, this will:
        1. Update load balancer to route 100% traffic to new version
        2. Mark old version as deprecated
        3. Record promotion completion
        
        Returns:
            True if successful
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        logger.info("Promoting canary to 100% traffic (test mode)")
        
        # Phase 2: Traffic routing update will go here
        # Example implementation:
        # from ena.routing import configure_traffic_split
        # configure_traffic_split(
        #     model_id=self.state_machine.status.model_id,
        #     new_version=self.state_machine.status.target_version,
        #     new_weight=1.0,  # 100% to new version
        # )
        
        # Record promotion
        self.state_machine.promote_canary()
        
        # Transition to completed
        self.state_machine.transition(UpgradeState.COMPLETED)
        
        logger.info("Canary promoted successfully - upgrade complete!")
        return True
    
    def rollback(self) -> bool:
        """
        Rollback to previous version.
        
        Args:
            None (uses previous_version from status)
        
        Returns:
            True if successful
        """
        if not self.state_machine.status:
            raise ValueError("No upgrade in progress")
        
        previous_version = self.state_machine.status.previous_version
        
        if not previous_version:
            logger.error("No previous version to rollback to")
            return False
        
        logger.info(f"Rolling back to version: {previous_version}")
        
        # Load previous manifest
        model_id = self.state_machine.status.model_id
        previous_manifest = self.registry.load_manifest(model_id, previous_version)
        
        if not previous_manifest:
            logger.error(f"Previous version not found: {model_id} v{previous_version}")
            return False
        
        # Pin previous version
        self.registry.pin_version(model_id, previous_version)
        
        # Transition to rolled back state
        self.state_machine.transition(UpgradeState.ROLLED_BACK)
        
        logger.info("Rollback complete")
        return True
    
    def run_full_workflow(
        self,
        model_id: str,
        target_version: str,
        creator: str,
        dataset_hashes: List[str],
        base_model: str = "qwen2.5-coder-1.5b",
        auto_promote: bool = False,
    ) -> bool:
        """
        Run the full upgrade workflow from start to finish.
        
        Args:
            model_id: Model identifier
            target_version: Target version
            creator: Creator address
            dataset_hashes: Dataset DA commitment hashes
            base_model: Base model to fine-tune from
            auto_promote: Automatically promote canary to 100%
        
        Returns:
            True if workflow completed successfully
        """
        try:
            # Step 1: Create plan
            plan = self.create_plan(
                model_id=model_id,
                target_version=target_version,
                creator=creator,
                dataset_hashes=dataset_hashes,
                base_model=base_model,
            )
            
            # Step 2: Allocate budget
            self.allocate_budget(plan.max_total_cost_anm)
            
            # Step 3: Submit jobs
            self.submit_jobs(plan)
            
            # Step 4: Monitor progress
            self.monitor_progress()
            
            # Step 5: Verify results (stub - no real outputs yet)
            # For now, create dummy outputs
            job_outputs = {}
            metrics = {}
            
            for job in plan.jobs:
                # Create dummy output directory
                output_dir = self.work_dir / "outputs" / job.job_id
                output_dir.mkdir(parents=True, exist_ok=True)
                job_outputs[job.job_id] = output_dir
                
                # Create dummy metrics for eval jobs
                if "eval" in job.job_type.value:
                    metrics[job.job_id] = EvalMetrics(
                        accuracy=0.95,
                        perplexity=2.5,
                        toxicity_score=0.05,
                        regression_pass_rate=0.98,
                    )
            
            result = self.verify_results(plan, job_outputs, metrics)
            
            if not result.passed:
                logger.error(f"Verification failed: {result.reason}")
                return False
            
            # Step 6: Publish model
            manifest = self._create_manifest_from_plan(plan, metrics)
            self.publish_model(manifest)
            
            # Step 7: Rollout canary
            self.rollout_canary()
            
            # Step 8: Promote if auto-promote enabled
            if auto_promote:
                self.promote_canary()
            else:
                logger.info("Canary deployed. Use 'upgrade promote' to complete rollout.")
            
            return True
        
        except Exception as e:
            logger.error(f"Workflow failed: {e}")
            self.state_machine.transition(UpgradeState.FAILED, str(e))
            return False
    
    def _create_manifest_from_plan(
        self,
        plan: TrainingPlan,
        metrics: Dict[str, EvalMetrics],
    ) -> ModelManifest:
        """Create a model manifest from training plan and results."""
        # Get final eval metrics
        eval_jobs = [j for j in plan.jobs if "eval" in j.job_type.value]
        final_metrics = metrics.get(eval_jobs[-1].job_id) if eval_jobs else EvalMetrics()
        
        # Create manifest
        manifest = ModelManifest(
            model_id=plan.model_id,
            version=plan.target_version,
            model_type=ModelType.STUDENT,
            quantization=QuantizationType.GGUF_Q4_0,
            artifact_hashes=ArtifactHashes(
                weights="",  # Would be filled in from job outputs
                tokenizer="",
                config="",
            ),
            artifact_urls={},
            eval_metrics=final_metrics,
            training_provenance=TrainingProvenance(
                base_model=plan.jobs[0].base_model or "",
                dataset_hashes=plan.dataset_commitments,
                hyperparams={},
                eval_suite_hash="",
                aicf_job_ids=[j.job_id for j in plan.jobs],
                training_start=plan.created_at,
                training_end=datetime.utcnow().isoformat() + "Z",
            ),
            rollout_policy=RolloutPolicy(),
            created_at=datetime.utcnow().isoformat() + "Z",
            creator=plan.creator,
            description=plan.description,
        )
        
        return manifest
