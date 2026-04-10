"""
AICF Protocol RPC Methods
==========================

JSON-RPC methods for the AICF GPU redistribution protocol.

Exposed methods:
  • aicf.protocol.registerWorker
  • aicf.protocol.updateWorker
  • aicf.protocol.getWorker
  • aicf.protocol.listWorkers
  • aicf.protocol.createJob
  • aicf.protocol.listJobs
  • aicf.protocol.getJob
  • aicf.protocol.submitWork
  • aicf.protocol.getSubmission
  • aicf.protocol.challengeSubmission
  • aicf.protocol.resolveChallenge
  • aicf.protocol.finalizeEpoch
  • aicf.protocol.getEpoch
  • aicf.protocol.claim
  • aicf.protocol.getStatus

All methods are transport-agnostic and return JSON-serializable structures.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from aicf.errors import AICFError
from aicf.protocol.state import (
    ProtocolState,
    GPUWorker,
    TrainingJob,
    WorkSubmission,
    SubmissionChallenge,
)
from aicf.protocol.economics import EpochAccountant


class ProtocolRPCMethods:
    """RPC method implementations for AICF protocol."""

    def __init__(self, state: ProtocolState, accountant: EpochAccountant):
        """
        Initialize protocol RPC methods.
        
        Args:
            state: Protocol state manager
            accountant: Epoch accountant for economics
        """
        self.state = state
        self.accountant = accountant

    def make_methods(self) -> Dict[str, Callable[..., Any]]:
        """
        Build a mapping of JSON-RPC method name -> callable.
        """

        def register_worker(
            *,
            address: str,
            pubkey: Optional[str] = None,
            displayName: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            stakeTxHash: Optional[str] = None,
            stakeAmount: int = 0,
            region: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Register a new GPU worker."""
            if not address:
                raise AICFError("address is required")
            
            worker_id = f"worker_{uuid4().hex[:16]}"
            
            worker = GPUWorker(
                worker_id=worker_id,
                address=address,
                pubkey=pubkey,
                display_name=displayName,
                metadata=metadata,
                stake_tx_hash=stakeTxHash,
                stake_amount=stakeAmount,
                status="ACTIVE" if stakeAmount > 0 else "INACTIVE",
                region=region,
            )
            
            self.state.register_worker(worker)
            
            return {
                "workerId": worker_id,
                "address": address,
                "status": worker.status,
            }

        def update_worker(
            *,
            workerId: str,
            displayName: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            status: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Update worker fields."""
            if not workerId:
                raise AICFError("workerId is required")
            
            # Build update dict
            updates = {}
            if displayName is not None:
                updates["display_name"] = displayName
            if metadata is not None:
                updates["metadata"] = metadata
            if status is not None:
                updates["status"] = status
            
            self.state.update_worker(workerId, **updates)
            
            worker = self.state.get_worker(workerId)
            
            return {
                "workerId": worker.worker_id,
                "address": worker.address,
                "displayName": worker.display_name,
                "status": worker.status,
                "stakeAmount": worker.stake_amount,
                "region": worker.region,
                "updatedAt": worker.updated_at,
            }

        def get_worker(*, workerId: str) -> Dict[str, Any]:
            """Get worker details."""
            if not workerId:
                raise AICFError("workerId is required")
            
            worker = self.state.get_worker(workerId)
            
            return {
                "workerId": worker.worker_id,
                "address": worker.address,
                "pubkey": worker.pubkey,
                "displayName": worker.display_name,
                "metadata": worker.metadata,
                "stakeTxHash": worker.stake_tx_hash,
                "stakeAmount": worker.stake_amount,
                "status": worker.status,
                "region": worker.region,
                "createdAt": worker.created_at,
                "updatedAt": worker.updated_at,
            }

        def list_workers(
            *,
            status: Optional[str] = None,
            offset: int = 0,
            limit: int = 100,
        ) -> Dict[str, Any]:
            """List GPU workers."""
            workers = self.state.list_workers(
                status=status,
                offset=offset,
                limit=limit,
            )
            
            items = []
            for w in workers:
                items.append({
                    "workerId": w.worker_id,
                    "address": w.address,
                    "displayName": w.display_name,
                    "stakeAmount": w.stake_amount,
                    "status": w.status,
                    "region": w.region,
                    "createdAt": w.created_at,
                })
            
            return {
                "items": items,
                "nextOffset": offset + len(items),
            }

        def create_job(
            *,
            specHash: str,
            datasetCommit: Optional[str] = None,
            jobType: str = "TRAINING",
            difficulty: int = 1,
            rewardWeight: int = 100,
            expiresAt: Optional[int] = None,
            creator: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Create a new training/eval job."""
            if not specHash:
                raise AICFError("specHash is required")
            
            job_id = f"job_{uuid4().hex[:16]}"
            
            job = TrainingJob(
                job_id=job_id,
                spec_hash=specHash,
                dataset_commit=datasetCommit,
                job_type=jobType,
                difficulty=difficulty,
                reward_weight=rewardWeight,
                expires_at=expiresAt,
                creator=creator,
                status="OPEN",
            )
            
            self.state.create_job(job)
            
            return {
                "jobId": job_id,
                "specHash": specHash,
                "jobType": jobType,
                "status": "OPEN",
            }

        def list_jobs(
            *,
            status: Optional[str] = None,
            jobType: Optional[str] = None,
            offset: int = 0,
            limit: int = 100,
        ) -> Dict[str, Any]:
            """List training/eval jobs."""
            jobs = self.state.list_jobs(
                status=status,
                job_type=jobType,
                offset=offset,
                limit=limit,
            )
            
            items = []
            for j in jobs:
                items.append({
                    "jobId": j.job_id,
                    "specHash": j.spec_hash,
                    "datasetCommit": j.dataset_commit,
                    "jobType": j.job_type,
                    "difficulty": j.difficulty,
                    "rewardWeight": j.reward_weight,
                    "status": j.status,
                    "createdAt": j.created_at,
                    "expiresAt": j.expires_at,
                })
            
            return {
                "items": items,
                "nextOffset": offset + len(items),
            }

        def get_job(*, jobId: str) -> Dict[str, Any]:
            """Get job details."""
            if not jobId:
                raise AICFError("jobId is required")
            
            job = self.state.get_job(jobId)
            
            return {
                "jobId": job.job_id,
                "specHash": job.spec_hash,
                "datasetCommit": job.dataset_commit,
                "jobType": job.job_type,
                "difficulty": job.difficulty,
                "rewardWeight": job.reward_weight,
                "status": job.status,
                "createdAt": job.created_at,
                "expiresAt": job.expires_at,
                "creator": job.creator,
            }

        def submit_work(
            *,
            jobId: str,
            workerId: str,
            artifactCommit: str,
            metrics: Optional[Dict[str, Any]] = None,
            proofCommit: Optional[str] = None,
            currentHeight: int = 0,
        ) -> Dict[str, Any]:
            """Submit work for a job."""
            if not jobId or not workerId or not artifactCommit:
                raise AICFError("jobId, workerId, and artifactCommit are required")
            
            # Get challenge window from params
            challenge_window = int(self.state.get_param("challenge_window_blocks", "100"))
            challenge_deadline = currentHeight + challenge_window
            
            submission_id = f"sub_{uuid4().hex[:16]}"
            
            submission = WorkSubmission(
                submission_id=submission_id,
                job_id=jobId,
                worker_id=workerId,
                artifact_commit=artifactCommit,
                metrics=metrics,
                proof_commit=proofCommit,
                status="PENDING",
                challenge_deadline=challenge_deadline,
            )
            
            self.state.submit_work(submission)
            
            return {
                "submissionId": submission_id,
                "jobId": jobId,
                "workerId": workerId,
                "status": "PENDING",
                "challengeDeadline": challenge_deadline,
            }

        def get_submission(*, submissionId: str) -> Dict[str, Any]:
            """Get submission details."""
            if not submissionId:
                raise AICFError("submissionId is required")
            
            sub = self.state.get_submission(submissionId)
            
            return {
                "submissionId": sub.submission_id,
                "jobId": sub.job_id,
                "workerId": sub.worker_id,
                "artifactCommit": sub.artifact_commit,
                "metrics": sub.metrics,
                "proofCommit": sub.proof_commit,
                "status": sub.status,
                "postedAt": sub.posted_at,
                "challengeDeadline": sub.challenge_deadline,
                "verifiedBy": sub.verified_by,
                "rejectionReason": sub.rejection_reason,
                "creditsAwarded": sub.credits_awarded,
            }

        def challenge_submission(
            *,
            submissionId: str,
            challengerAddress: str,
            challengeDataCommit: str,
        ) -> Dict[str, Any]:
            """Challenge a work submission."""
            if not submissionId or not challengerAddress or not challengeDataCommit:
                raise AICFError("submissionId, challengerAddress, and challengeDataCommit are required")
            
            challenge_id = f"chal_{uuid4().hex[:16]}"
            
            challenge = SubmissionChallenge(
                challenge_id=challenge_id,
                submission_id=submissionId,
                challenger_address=challengerAddress,
                challenge_data_commit=challengeDataCommit,
                status="OPEN",
            )
            
            self.state.create_challenge(challenge)
            
            # Update submission status
            self.state.update_submission(submissionId, status="CHALLENGED")
            
            return {
                "challengeId": challenge_id,
                "submissionId": submissionId,
                "status": "OPEN",
            }

        def resolve_challenge(
            *,
            challengeId: str,
            status: str,
            resolutionCommit: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Resolve a challenge."""
            if not challengeId or not status:
                raise AICFError("challengeId and status are required")
            
            if status not in ["RESOLVED_VALID", "RESOLVED_INVALID", "EXPIRED"]:
                raise AICFError(f"Invalid resolution status: {status}")
            
            self.state.resolve_challenge(challengeId, status, resolutionCommit)
            
            return {
                "challengeId": challengeId,
                "status": status,
            }

        def finalize_epoch(
            *,
            epochId: int,
            endHeight: int,
        ) -> Dict[str, Any]:
            """Finalize an epoch and compute rewards."""
            if epochId is None or endHeight is None:
                raise AICFError("epochId and endHeight are required")
            
            # Finalize epoch
            worker_rewards = self.accountant.finalize_epoch(epochId, endHeight)
            
            # Get stats
            stats = self.accountant.get_epoch_stats(epochId)
            
            return {
                "epochId": epochId,
                "endHeight": endHeight,
                "inflowTotal": stats["inflow_total"],
                "inflowForWorkers": stats["inflow_for_workers"],
                "totalCredits": stats["total_credits"],
                "workerCount": stats["worker_count"],
                "finalized": True,
                "workerRewards": worker_rewards,
            }

        def get_epoch(*, epochId: int) -> Dict[str, Any]:
            """Get epoch details."""
            if epochId is None:
                raise AICFError("epochId is required")
            
            stats = self.accountant.get_epoch_stats(epochId)
            
            return stats

        def claim(
            *,
            epochId: int,
            workerId: str,
            merkleProof: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Claim rewards for an epoch."""
            if epochId is None or not workerId:
                raise AICFError("epochId and workerId are required")
            
            claim_id, amount = self.accountant.create_claim(
                epochId,
                workerId,
                merkleProof,
            )
            
            if not claim_id:
                return {
                    "claimed": False,
                    "amount": "0",
                    "reason": "No credits or already claimed",
                }
            
            return {
                "claimId": claim_id,
                "epochId": epochId,
                "workerId": workerId,
                "amount": amount,
                "claimed": True,
                "status": "PENDING",
            }

        def get_status(self) -> Dict[str, Any]:
            """Get protocol status."""
            params = self.state.get_all_params()
            
            # Get current epoch (rough estimation)
            # In production, this would come from chain height
            current_epoch = 0
            
            return {
                "params": {
                    "epochLengthBlocks": int(params.get("epoch_length_blocks", "1000")),
                    "challengeWindowBlocks": int(params.get("challenge_window_blocks", "100")),
                    "minStake": params.get("min_stake", "1000000000"),
                    "maxWorkers": int(params.get("max_workers", "1000")),
                    "rewardSplit": {
                        "gpuWorkersBp": int(params.get("reward_split_gpu_workers_bp", "7000")),
                        "treasuryBp": int(params.get("reward_split_treasury_bp", "2000")),
                        "devBp": int(params.get("reward_split_dev_bp", "500")),
                        "burnBp": int(params.get("reward_split_burn_bp", "500")),
                    },
                    "verifyPolicy": params.get("verify_policy", "mvp_challenge_window"),
                },
                "currentEpoch": current_epoch,
                "totalWorkers": len(self.state.list_workers(status="ACTIVE")),
            }

        # Return method mapping
        return {
            "aicf.protocol.registerWorker": register_worker,
            "aicf.protocol.updateWorker": update_worker,
            "aicf.protocol.getWorker": get_worker,
            "aicf.protocol.listWorkers": list_workers,
            "aicf.protocol.createJob": create_job,
            "aicf.protocol.listJobs": list_jobs,
            "aicf.protocol.getJob": get_job,
            "aicf.protocol.submitWork": submit_work,
            "aicf.protocol.getSubmission": get_submission,
            "aicf.protocol.challengeSubmission": challenge_submission,
            "aicf.protocol.resolveChallenge": resolve_challenge,
            "aicf.protocol.finalizeEpoch": finalize_epoch,
            "aicf.protocol.getEpoch": get_epoch,
            "aicf.protocol.claim": claim,
            "aicf.protocol.getStatus": get_status,
        }
