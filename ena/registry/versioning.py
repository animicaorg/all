"""
Version parsing and comparison utilities.
"""

import re
from typing import Tuple, Optional


def parse_version(version: str) -> Tuple[int, ...]:
    """
    Parse version string to tuple of integers.
    
    Supports:
    - Semver: "1.2.3" -> (1, 2, 3)
    - Epoch-based: "epoch-42" -> (42,)
    - Pre-release: "1.2.3-beta.1" -> (1, 2, 3, 0, 1) # beta < rc < release
    
    Args:
        version: Version string
    
    Returns:
        Tuple of integers for comparison
    
    Raises:
        ValueError: If version format is invalid
    """
    # Handle epoch-based versions
    if version.startswith("epoch-"):
        try:
            epoch = int(version[6:])
            return (epoch,)
        except ValueError:
            raise ValueError(f"Invalid epoch version: {version}")
    
    # Handle semver
    semver_pattern = r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)(?:\.(\d+))?)?$"
    match = re.match(semver_pattern, version)
    
    if not match:
        raise ValueError(f"Invalid version format: {version}. Expected semver or epoch-N")
    
    major, minor, patch, pre_release, pre_num = match.groups()
    result = [int(major), int(minor), int(patch)]
    
    # Pre-release versions are lower than release versions
    # alpha < beta < rc < (release)
    if pre_release:
        pre_release_order = {"alpha": 1, "beta": 2, "rc": 3}
        result.append(pre_release_order[pre_release])
        if pre_num:
            result.append(int(pre_num))
        else:
            result.append(0)
    else:
        # Release version gets high value
        result.append(999)
    
    return tuple(result)


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.
    
    Args:
        v1: First version
        v2: Second version
    
    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)
    
    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    else:
        return 0


def is_valid_version(version: str) -> bool:
    """Check if version string is valid."""
    try:
        parse_version(version)
        return True
    except ValueError:
        return False


def get_next_version(current: str, bump: str = "patch") -> str:
    """
    Get next version string by bumping current version.
    
    Args:
        current: Current version (semver only, not epoch)
        bump: "major", "minor", or "patch"
    
    Returns:
        Next version string
    
    Raises:
        ValueError: If current version is not semver or bump is invalid
    """
    if current.startswith("epoch-"):
        raise ValueError("Cannot bump epoch-based versions. Use epoch-N+1 instead.")
    
    parts = parse_version(current)
    if len(parts) < 3:
        raise ValueError(f"Invalid semver: {current}")
    
    major, minor, patch = parts[0], parts[1], parts[2]
    
    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Invalid bump type: {bump}. Must be 'major', 'minor', or 'patch'")
