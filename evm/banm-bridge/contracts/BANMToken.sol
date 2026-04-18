// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Burnable} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

contract BANMToken is ERC20, ERC20Burnable, AccessControl, Pausable {
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant BURNER_ROLE = keccak256("BURNER_ROLE");

    event MintExecuted(bytes32 indexed orderId, address indexed to, uint256 amount);
    event BurnExecuted(bytes32 indexed orderId, address indexed account, uint256 amount);

    constructor(address admin) ERC20("BANM", "BANM") {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    function decimals() public pure override returns (uint8) {
        return 18;
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function mint(address to, uint256 amount, bytes32 orderId) external onlyRole(MINTER_ROLE) whenNotPaused {
        _mint(to, amount);
        emit MintExecuted(orderId, to, amount);
    }

    function bridgeBurn(uint256 amount, bytes32 orderId) external onlyRole(BURNER_ROLE) whenNotPaused {
        _burn(msg.sender, amount);
        emit BurnExecuted(orderId, msg.sender, amount);
    }

    function _update(address from, address to, uint256 value) internal override whenNotPaused {
        super._update(from, to, value);
    }
}

