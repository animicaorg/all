// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {BANMToken} from "./BANMToken.sol";
import {BANMBridgeVault} from "./BANMBridgeVault.sol";

contract BANMBridgeDepositRouter is AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for BANMToken;

    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");

    BANMToken public immutable token;
    BANMBridgeVault public immutable vault;

    mapping(bytes32 => bool) public depositRegistered;

    event DepositRegistered(
        bytes32 indexed orderId,
        address indexed sender,
        uint256 amount,
        address indexed vault
    );

    constructor(address admin, BANMToken token_, BANMBridgeVault vault_) {
        token = token_;
        vault = vault_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(OPERATOR_ROLE, admin);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function deposit(bytes32 orderId, uint256 amount) external whenNotPaused nonReentrant {
        require(amount > 0, "amount must be > 0");
        require(!depositRegistered[orderId], "order already deposited");
        depositRegistered[orderId] = true;

        token.safeTransferFrom(msg.sender, address(vault), amount);
        vault.registerDeposit(orderId, msg.sender, amount);
        emit DepositRegistered(orderId, msg.sender, amount, address(vault));
    }
}

