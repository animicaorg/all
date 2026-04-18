// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

import {BANMToken} from "./BANMToken.sol";

contract BANMBridgeController is AccessControl, Pausable {
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 public constant BURNER_ROLE = keccak256("BURNER_ROLE");
    bytes32 public constant VAULT_MANAGER_ROLE = keccak256("VAULT_MANAGER_ROLE");

    BANMToken public immutable token;

    uint256 public dailyMintCap;
    uint256 public dailyReleaseCap;
    uint256 public mintedToday;
    uint256 public releasedToday;
    uint256 public capDay;

    mapping(bytes32 => bool) public mintExecutedForOrder;
    mapping(bytes32 => bool) public burnExecutedForOrder;
    mapping(bytes32 => bool) public releaseCompletedForOrder;

    struct ChainConfig {
        bool enabled;
        address router;
        address vault;
        string label;
    }

    mapping(uint256 => ChainConfig) public chainConfigs;

    event MintRequested(
        bytes32 indexed orderId,
        address indexed to,
        uint256 amount,
        uint256 feeAmount,
        string externalRef
    );
    event MintExecuted(
        bytes32 indexed orderId,
        address indexed to,
        uint256 amount,
        uint256 feeAmount,
        string externalRef
    );
    event MintRejected(bytes32 indexed orderId, string reason);

    event BurnRequested(bytes32 indexed orderId, address indexed from, uint256 amount, string externalRef);
    event BurnExecuted(bytes32 indexed orderId, address indexed from, uint256 amount, string externalRef);

    event ReleaseRequested(bytes32 indexed orderId, string animicaAddress, uint256 amount, string externalRef);
    event ReleaseCompleted(
        bytes32 indexed orderId,
        string animicaAddress,
        uint256 amount,
        string animicaTxHash
    );

    event ChainConfigured(uint256 indexed chainId, bool enabled, address router, address vault, string label);
    event CapsUpdated(uint256 dailyMintCap, uint256 dailyReleaseCap);

    constructor(address admin, BANMToken token_) {
        token = token_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(OPERATOR_ROLE, admin);
        _grantRole(MINTER_ROLE, admin);
        _grantRole(BURNER_ROLE, admin);
        _grantRole(VAULT_MANAGER_ROLE, admin);
        capDay = _day();
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function setCaps(uint256 dailyMintCap_, uint256 dailyReleaseCap_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        dailyMintCap = dailyMintCap_;
        dailyReleaseCap = dailyReleaseCap_;
        emit CapsUpdated(dailyMintCap_, dailyReleaseCap_);
    }

    function configureChain(
        uint256 chainId,
        bool enabled,
        address router,
        address vault,
        string calldata label
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        chainConfigs[chainId] = ChainConfig({
            enabled: enabled,
            router: router,
            vault: vault,
            label: label
        });
        emit ChainConfigured(chainId, enabled, router, vault, label);
    }

    function executeMint(
        bytes32 orderId,
        address to,
        uint256 amount,
        uint256 feeAmount,
        string calldata externalRef
    ) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        _rolloverDay();
        require(!mintExecutedForOrder[orderId], "mint already executed for order");
        if (dailyMintCap > 0) {
            require(mintedToday + amount <= dailyMintCap, "daily mint cap exceeded");
        }
        emit MintRequested(orderId, to, amount, feeAmount, externalRef);
        mintExecutedForOrder[orderId] = true;
        mintedToday += amount;
        token.mint(to, amount, orderId);
        emit MintExecuted(orderId, to, amount, feeAmount, externalRef);
    }

    function rejectMint(bytes32 orderId, string calldata reason) external onlyRole(OPERATOR_ROLE) {
        emit MintRejected(orderId, reason);
    }

    function registerBurn(
        bytes32 orderId,
        address from,
        uint256 amount,
        string calldata externalRef
    ) external onlyRole(VAULT_MANAGER_ROLE) {
        require(!burnExecutedForOrder[orderId], "burn already executed for order");
        emit BurnRequested(orderId, from, amount, externalRef);
        burnExecutedForOrder[orderId] = true;
        emit BurnExecuted(orderId, from, amount, externalRef);
    }

    function markReleaseRequested(
        bytes32 orderId,
        string calldata animicaAddress,
        uint256 amount,
        string calldata externalRef
    ) external onlyRole(OPERATOR_ROLE) {
        emit ReleaseRequested(orderId, animicaAddress, amount, externalRef);
    }

    function markReleaseCompleted(
        bytes32 orderId,
        string calldata animicaAddress,
        uint256 amount,
        string calldata animicaTxHash
    ) external onlyRole(OPERATOR_ROLE) {
        _rolloverDay();
        require(!releaseCompletedForOrder[orderId], "release already completed for order");
        if (dailyReleaseCap > 0) {
            require(releasedToday + amount <= dailyReleaseCap, "daily release cap exceeded");
        }
        releaseCompletedForOrder[orderId] = true;
        releasedToday += amount;
        emit ReleaseCompleted(orderId, animicaAddress, amount, animicaTxHash);
    }

    function _day() internal view returns (uint256) {
        return block.timestamp / 1 days;
    }

    function _rolloverDay() internal {
        uint256 today = _day();
        if (today != capDay) {
            capDay = today;
            mintedToday = 0;
            releasedToday = 0;
        }
    }
}

