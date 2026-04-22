from __future__ import annotations

import pytest


def test_project_balance_flow(compile_contract):
    c = compile_contract('contracts/packages/aicf_project_balance/contract.py')

    owner = b'owner'
    project = b'project-1'
    job = b'job-1'

    c.call('init', owner)
    c.call('deposit_project', owner, project, 1_000_000)
    c.call('reserve_for_job', owner, project, job, 300_000)

    bal = c.call('project_balance', project)
    assert bal['available_anm_nanos'] == 700_000
    assert bal['reserved_anm_nanos'] == 300_000

    c.call('settle_job', owner, project, job, 220_000, 80_000)

    bal = c.call('project_balance', project)
    assert bal['available_anm_nanos'] == 780_000
    assert bal['reserved_anm_nanos'] == 0
    assert bal['spent_anm_nanos'] == 220_000
    assert bal['refunded_anm_nanos'] == 80_000

    c.call('withdraw_project', owner, project, 100_000)
    bal = c.call('project_balance', project)
    assert bal['available_anm_nanos'] == 680_000


def test_job_escrow_replay_protection(compile_contract):
    c = compile_contract('contracts/packages/aicf_job_escrow/contract.py')

    owner = b'owner'
    c.call('init', owner)

    job_id = b'job-a'
    c.call('open_job_escrow', owner, job_id, b'project-a', b'provider-a', 500_000, 20_000)

    c.call('settle_job_escrow', owner, b'settle-1', job_id, 330_000, 100_000, 70_000)

    info = c.call('escrow_info', job_id)
    assert info['state'] == 2

    with pytest.raises(Exception):
        c.call('settle_job_escrow', owner, b'settle-1', job_id, 1, 1, 1)


def test_provider_registry_and_stake_and_rewards(compile_contract):
    registry = compile_contract('contracts/packages/aicf_provider_registry/contract.py')
    stake = compile_contract('contracts/packages/aicf_stake_manager/contract.py')
    rewards = compile_contract('contracts/packages/aicf_rewards/contract.py')

    owner = b'owner'
    provider = b'provider-1'
    node = b'node-1'

    registry.call('init', owner)
    registry.call('register_provider', owner, provider, b'anm1provider', b'cap://gpu')
    registry.call('register_node', owner, provider, node, b'meta://node')
    registry.call('heartbeat_node', owner, node, 123)

    pinfo = registry.call('provider_info', provider)
    ninfo = registry.call('node_info', node)
    assert pinfo['exists'] is True
    assert ninfo['exists'] is True
    assert ninfo['last_heartbeat'] == 123

    stake.call('init', owner, 1_000, 5)
    stake.call('stake_for_provider', owner, provider, 2_000)
    stake.call('request_unstake', owner, provider, 500, 100)

    with pytest.raises(Exception):
        stake.call('finalize_unstake', owner, provider, 102)

    unstaked = stake.call('finalize_unstake', owner, provider, 105)
    assert unstaked == 500

    slashed = stake.call('slash_provider', owner, provider, 400, b'bad_receipt')
    assert slashed == 400

    rewards.call('init', owner)
    rewards.call('credit_reward', owner, provider, 1_200, b'settle-abc', 100)
    assert rewards.call('reward_balance', provider) == 1_200

    claimed = rewards.call('claim_rewards', owner, provider, b'claim-1')
    assert claimed == 1_200
    assert rewards.call('reward_balance', provider) == 0



def test_dispute_governance_and_model_registry(compile_contract):
    dispute = compile_contract('contracts/packages/aicf_dispute_manager/contract.py')
    governance = compile_contract('contracts/packages/aicf_governance_config/contract.py')
    model_registry = compile_contract('contracts/packages/aicf_model_registry/contract.py')

    owner = b'owner'

    dispute.call('init', owner, 10)
    dispute.call('open_dispute', owner, b'dispute-1', b'job-1', b'incorrect_output', 42)
    dispute.call('resolve_dispute', owner, b'dispute-1', 2, 75, 52)

    dinfo = dispute.call('dispute_info', b'dispute-1')
    assert dinfo['state'] == 3

    governance.call('init', owner)
    governance.call('set_param_u64', owner, b'min_stake', 1_500)
    governance.call('set_param_bytes', owner, b'model_mode', b'provider_network')
    governance.call('set_paused', owner, True, b'emergency')

    assert governance.call('paused') is True
    assert governance.call('get_param_u64', b'min_stake') == 1_500
    assert governance.call('get_param_bytes', b'model_mode') == b'provider_network'

    model_registry.call('init', owner)
    model_registry.call(
        'register_model',
        owner,
        b'aicf-chat-1',
        b'aicf-chat-1',
        b'2026-04-22',
        b'pricing://aicf-chat-1',
        b'runtime://vllm',
    )
    model_registry.call('set_model_status', owner, b'aicf-chat-1', 2)

    minfo = model_registry.call('model_info', b'aicf-chat-1')
    assert minfo['exists'] is True
    assert minfo['status'] == 2


def test_model_call_contract_lifecycle(compile_contract):
    c = compile_contract('contracts/packages/aicf_model_call/contract.py')
    owner = b'owner'

    c.call('init', owner)
    c.call(
        'request_model_call',
        owner,
        b'req-1',
        b'call-1',
        b'contract-1',
        b'payer-1',
        b'aicf-chat-1',
        b'model_call',
        b'0xinputhash',
        b'schema://json',
        1_000_000,
        1_000,
        1,
        1,
        1,
        10,
        b'open',
        1,
        0,
    )
    c.call('claim_call', owner, b'call-1', b'provider-1', 900)
    c.call(
        'submit_result_commitment',
        owner,
        b'commit-1',
        b'call-1',
        b'provider-1',
        b'0xresult',
        b'sig-provider',
        b'meta://usage',
        910,
    )
    c.call('submit_result_reference', owner, b'ref-1', b'call-1', b's3://bucket/result.json')
    c.call('finalize_result', owner, b'call-1', 921)

    info = c.call('call_info', b'call-1')
    assert info['state'] == 7
    assert info['result_hash'] == b'0xresult'
    assert info['result_ref'] == b's3://bucket/result.json'


def test_agent_task_contract_lifecycle(compile_contract):
    c = compile_contract('contracts/packages/aicf_agent_task/contract.py')
    owner = b'owner'

    c.call('init', owner)
    c.call('create_agent_task', owner, b'create-1', b'task-1', b'requester-1', b'payer-1', b'aicf-chat-1')
    c.call('fund_agent_task', owner, b'fund-1', b'task-1', 2_000_000)
    c.call('start_agent_task', owner, b'task-1', 15, 100)
    c.call(
        'append_step_commitment',
        owner,
        b'step-1',
        b'task-1',
        500_000,
        b'0xstep1',
        b'trace://step1',
        105,
    )
    c.call('submit_final_result', owner, b'final-1', b'task-1', b'0xfinal', b's3://result/final.json')
    c.call('finalize_agent_task', owner, b'finalize-1', b'task-1', 1_200_000)

    info = c.call('task_info', b'task-1')
    assert info['state'] == 6
    assert info['step_count'] == 1
    assert info['final_result_hash'] == b'0xfinal'
