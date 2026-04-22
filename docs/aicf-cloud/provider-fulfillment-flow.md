# Provider Fulfillment Flow

1. Provider registers and stakes ANM.
2. Fulfillment scheduler assigns contract job.
3. Provider daemon claims `/provider/daemon/contract-jobs/claim`.
4. Provider executes runtime off-chain.
5. Provider submits commitment (`resultHash`, signature, usage).
6. Provider submits result reference.
7. Finalization/dispute pipeline settles ANM outcome.
