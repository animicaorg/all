"""{{CONTRACT_NAME}} — Token-weighted governance voting.

Author: {{AUTHOR}}

Voters are registered with a weight (stake). Proposals pass when yes_weight
exceeds the quorum_weight threshold set at deploy time.
"""

STORAGE = {
    "owner": "address",
    "weights": "map(address, int)",
    "total_weight": "int",
    "quorum_weight": "int",
    "proposals": "map(int, dict)",
    "proposal_count": "int",
    "votes": "map(string, int)",  # "pid:voter" -> weight cast (0 = not voted)
}

ABI = [
    {"name": "deploy",          "type": "constructor", "inputs": [{"name": "owner", "type": "address"}, {"name": "quorum_weight", "type": "int"}]},
    {"name": "set_weight",      "type": "function",    "inputs": [{"name": "voter", "type": "address"}, {"name": "weight", "type": "int"}]},
    {"name": "create_proposal", "type": "function",    "inputs": [{"name": "description", "type": "string"}], "outputs": [{"type": "int"}]},
    {"name": "vote",            "type": "function",    "inputs": [{"name": "proposal_id", "type": "int"}, {"name": "support", "type": "bool"}]},
    {"name": "tally",           "type": "function",    "inputs": [{"name": "proposal_id", "type": "int"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "WeightSet",       "type": "event",       "inputs": [{"name": "voter", "type": "address"}, {"name": "weight", "type": "int"}]},
    {"name": "ProposalCreated", "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "description", "type": "string"}]},
    {"name": "VoteCast",        "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "voter", "type": "address"}, {"name": "weight", "type": "int"}, {"name": "support", "type": "bool"}]},
]


def deploy(ctx, owner: str, quorum_weight: int) -> None:
    if quorum_weight <= 0:
        raise ValueError("quorum_weight must be > 0")
    ctx.storage["owner"] = owner
    ctx.storage["quorum_weight"] = quorum_weight
    ctx.storage["weights"] = {}
    ctx.storage["total_weight"] = 0
    ctx.storage["proposals"] = {}
    ctx.storage["proposal_count"] = 0
    ctx.storage["votes"] = {}


def set_weight(ctx, voter: str, weight: int) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("only owner can set weights")
    if weight < 0:
        raise ValueError("weight must be >= 0")
    weights = ctx.storage.get("weights") or {}
    old = weights.get(voter, 0)
    weights[voter] = weight
    ctx.storage["weights"] = weights
    ctx.storage["total_weight"] = ctx.storage.get("total_weight", 0) - old + weight
    ctx.emit("WeightSet", {"voter": voter, "weight": weight})


def create_proposal(ctx, description: str) -> int:
    weights = ctx.storage.get("weights") or {}
    if not weights.get(ctx.caller):
        raise PermissionError("only registered voters can create proposals")
    proposals = ctx.storage.get("proposals") or {}
    pid = ctx.storage.get("proposal_count", 0) + 1
    proposals[pid] = {"id": pid, "description": description, "yes_weight": 0, "no_weight": 0}
    ctx.storage["proposals"] = proposals
    ctx.storage["proposal_count"] = pid
    ctx.emit("ProposalCreated", {"id": pid, "description": description})
    return pid


def vote(ctx, proposal_id: int, support: bool) -> None:
    weights = ctx.storage.get("weights") or {}
    my_weight = weights.get(ctx.caller, 0)
    if not my_weight:
        raise PermissionError("not a registered voter or zero weight")
    proposals = ctx.storage.get("proposals") or {}
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id} not found")
    vote_key = f"{proposal_id}:{ctx.caller}"
    votes = ctx.storage.get("votes") or {}
    if votes.get(vote_key, 0):
        raise RuntimeError("already voted on this proposal")
    if support:
        proposal["yes_weight"] = proposal.get("yes_weight", 0) + my_weight
    else:
        proposal["no_weight"] = proposal.get("no_weight", 0) + my_weight
    votes[vote_key] = my_weight
    ctx.storage["votes"] = votes
    proposals[proposal_id] = proposal
    ctx.storage["proposals"] = proposals
    ctx.emit("VoteCast", {"id": proposal_id, "voter": ctx.caller, "weight": my_weight, "support": support})


def tally(ctx, proposal_id: int) -> dict:
    proposals = ctx.storage.get("proposals") or {}
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id} not found")
    quorum = ctx.storage.get("quorum_weight", 1)
    yes_w = proposal.get("yes_weight", 0)
    no_w = proposal.get("no_weight", 0)
    return {
        "proposal_id": proposal_id,
        "description": proposal.get("description", ""),
        "yes_weight": yes_w,
        "no_weight": no_w,
        "quorum_weight": quorum,
        "passed": yes_w >= quorum,
    }

# CURSOR
