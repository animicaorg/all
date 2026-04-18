// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {BANMToken} from "./BANMToken.sol";
import {BANMBridgeController} from "./BANMBridgeController.sol";

contract BANMBridgeVault is AccessControl, Pausable, ReentrancyGuard {
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant BURNER_ROLE = keccak256("BURNER_ROLE");
    bytes32 public constant VAULT_MANAGER_ROLE = keccak256("VAULT_MANAGER_ROLE");
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 public constant ROUTER_ROLE = keccak256("ROUTER_ROLE");

    BANMToken public immutable token;
    BANMBridgeController public immutable controller;

    struct DepositRecord {
        address sender;
        uint256 amount;
        bool burned;
        uint64 timestamp;
    }

    mapping(bytes32 => DepositRecord) public deposits;

    event DepositRegistered(
        bytes32 indexed orderId,
        address indexed sender,
        uint256 amount,
        address indexed router
    );
    event BurnRequested(bytes32 indexed orderId, address indexed sender, uint256 amount);
    event BurnExecuted(bytes32 indexed orderId, address indexed sender, uint256 amount);

    constructor(address admin, BANMToken token_, BANMBridgeController controller_) {
        token = token_;
        controller = controller_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(BURNER_ROLE, admin);
        _grantRole(VAULT_MANAGER_ROLE, admin);
        _grantRole(OPERATOR_ROLE, admin);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function registerDeposit(bytes32 orderId, address sender, uint256 amount)
        external
        whenNotPaused
        onlyRole(ROUTER_ROLE)
    {
        require(amount > 0, "amount must be > 0");
        DepositRecord storage record = deposits[orderId];
        require(record.timestamp == 0, "deposit already registered for order");
        deposits[orderId] = DepositRecord({
            sender: sender,
            amount: amount,
            burned: false,
            timestamp: uint64(block.timestamp)
        });
        emit DepositRegistered(orderId, sender, amount, msg.sender);
    }

    function burnForOrder(bytes32 orderId)
        external
        nonReentrant
        whenNotPaused
        onlyRole(BURNER_ROLE)
    {
        DepositRecord storage record = deposits[orderId];
        require(record.timestamp > 0, "deposit not found");
        require(!record.burned, "order already burned");
        record.burned = true;

        emit BurnRequested(orderId, record.sender, record.amount);
        token.bridgeBurn(record.amount, orderId);
        controller.registerBurn(orderId, record.sender, record.amount, "vault-burn");
        emit BurnExecuted(orderId, record.sender, record.amount);
    }
}

