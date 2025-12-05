import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from governance.scripts import validate_proposal as validator


def test_governance_param_change_flow():
    # Simulate current module parameters
    module_params = {"min_gas_price": 1000}

    # Propose a deterministic change
    proposal = {"type": "param_change", "params": {"min_gas_price": 1200}}

    # Validate proposed change is well-formed and within bounds
    rules = {
        "min_gas_price": validator.BoundRule(min=1, max=1_000_000, step=1, type="int")
    }
    changes = validator._flatten_changes(proposal)
    assert changes == [("min_gas_price", 1200)]
    assert validator.validate_bounds(proposal, rules) == []

    # Simulate a simple vote tally
    votes_for, votes_against = 5, 2
    assert votes_for > votes_against

    # Apply the change and confirm it updates the module parameters
    for key, new_value in changes:
        module_params[key] = new_value

    assert module_params["min_gas_price"] == 1200
