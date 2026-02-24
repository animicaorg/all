"""{{CONTRACT_NAME}} — DAO proposal registry.

Author: {{AUTHOR}}
Quorum: {{QUORUM_PCT}}%

Members can create proposals, cast yes/no votes, and execute
proposals that reach quorum. All logic is deterministic and state-only.
"""

STORAGE = {
    "owner": "address",
    "members": "map(address, bool)",
    "member_count": "int",
    "proposals": "map(int, dict)",
    "proposal_count": "int",
    "votes": "map(string, bool)",  # "proposal_id:voter" -> voted
    "quorum_pct": "int",
}

ABI = [
    {"name": "deploy",          "type": "constructor", "inputs": [{"name": "owner", "type": "address"}, {"name": "quorum_pct", "type": "int"}]},
    {"name": "add_member",      "type": "function",    "inputs": [{"name": "member", "type": "address"}]},
    {"name": "remove_member",   "type": "function",    "inputs": [{"name": "member", "type": "address"}]},
    {"name": "create_proposal", "type": "function",    "inputs": [{"name": "description", "type": "string"}], "outputs": [{"type": "int"}]},
    {"name": "vote",            "type": "function",    "inputs": [{"name": "proposal_id", "type": "int"}, {"name": "support", "type": "bool"}]},
    {"name": "execute",         "type": "function",    "inputs": [{"name": "proposal_id", "type": "int"}]},
    {"name": "get_proposal",    "type": "function",    "inputs": [{"name": "proposal_id", "type": "int"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "ProposalCreated", "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "by", "type": "address"}, {"name": "description", "type": "string"}]},
    {"name": "VoteCast",        "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "voter", "type": "address"}, {"name": "support", "type": "bool"}]},
    {"name": "ProposalExecuted","type": "event",       "inputs": [{"name": "id", "type": "int"}]},
]


def deploy(ctx, owner: str, quorum_pct: int = 51) -> None:
    if not (1 <= quorum_pct <= 100):
        raise ValueError("quorum_pct must be 1..100")
    ctx.storage["owner"] = owner
    ctx.storage["quorum_pct"] = int("{{QUORUM_PCT}}" or quorum_pct)
    ctx.storage["members"] = {}
    ctx.storage["member_count"] = 0
    ctx.storage["proposals"] = {}
    ctx.storage["proposal_count"] = 0
    ctx.storage["votes"] = {}


def _require_owner(ctx) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("caller is not the owner")


def _require_member(ctx) -> None:
    members = ctx.storage.get("members") or {}
    if not members.get(ctx.caller):
        raise PermissionError("caller is not a member")


def add_member(ctx, member: str) -> None:
    _require_owner(ctx)
    members = ctx.storage.get("members") or {}
    if not members.get(member):
        members[member] = True
        ctx.storage["members"] = members
        ctx.storage["member_count"] = ctx.storage.get("member_count", 0) + 1


def remove_member(ctx, member: str) -> None:
    _require_owner(ctx)
    members = ctx.storage.get("members") or {}
    if members.pop(member, None):
        ctx.storage["members"] = members
        ctx.storage["member_count"] = max(0, ctx.storage.get("member_count", 1) - 1)


def create_proposal(ctx, description: str) -> int:
    _require_member(ctx)
    if not description:
        raise ValueError("description required")
    proposals = ctx.storage.get("proposals") or {}
    pid = ctx.storage.get("proposal_count", 0) + 1
    proposals[pid] = {
        "id": pid,
        "description": description,
        "proposer": ctx.caller,
        "yes": 0,
        "no": 0,
        "executed": False,
    }
    ctx.storage["proposals"] = proposals
    ctx.storage["proposal_count"] = pid
    ctx.emit("ProposalCreated", {"id": pid, "by": ctx.caller, "description": description})
    return pid


def vote(ctx, proposal_id: int, support: bool) -> None:
    _require_member(ctx)
    proposals = ctx.storage.get("proposals") or {}
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if proposal.get("executed"):
        raise RuntimeError("proposal already executed")
    vote_key = f"{proposal_id}:{ctx.caller}"
    votes = ctx.storage.get("votes") or {}
    if votes.get(vote_key):
        raise RuntimeError("already voted")
    if support:
        proposal["yes"] = proposal.get("yes", 0) + 1
    else:
        proposal["no"] = proposal.get("no", 0) + 1
    votes[vote_key] = True
    ctx.storage["votes"] = votes
    proposals[proposal_id] = proposal
    ctx.storage["proposals"] = proposals
    ctx.emit("VoteCast", {"id": proposal_id, "voter": ctx.caller, "support": support})


def execute(ctx, proposal_id: int) -> None:
    _require_member(ctx)
    proposals = ctx.storage.get("proposals") or {}
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if proposal.get("executed"):
        raise RuntimeError("proposal already executed")
    total = proposal.get("yes", 0) + proposal.get("no", 0)
    member_count = ctx.storage.get("member_count", 1)
    quorum = ctx.storage.get("quorum_pct", 51)
    needed = (member_count * quorum + 99) // 100
    if proposal.get("yes", 0) < needed:
        raise RuntimeError(f"quorum not reached: {proposal.get('yes', 0)}/{needed} yes votes needed")
    proposal["executed"] = True
    proposals[proposal_id] = proposal
    ctx.storage["proposals"] = proposals
    ctx.emit("ProposalExecuted", {"id": proposal_id})


def get_proposal(ctx, proposal_id: int) -> dict:
    proposals = ctx.storage.get("proposals") or {}
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id} not found")
    return proposal

# CURSOR
