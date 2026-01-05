#!/usr/bin/env bash
# Test that Docker containers run as non-root users

set -e

echo "================================"
echo "Testing Docker Non-Root Users"
echo "================================"
echo

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

test_passed=0
test_failed=0

# Function to test if a container runs as non-root
test_container_user() {
    local dockerfile=$1
    local expected_uid=$2
    local service_name=$3
    
    echo "Testing $service_name..."
    
    # Build the image
    if ! docker build -f "$dockerfile" -t "animica-$service_name:test" . >/dev/null 2>&1; then
        echo -e "${RED}✗ Failed to build $service_name image${NC}"
        ((test_failed++))
        return 1
    fi
    
    # Check the user
    uid=$(docker run --rm "animica-$service_name:test" id -u 2>/dev/null)
    
    if [ "$uid" -eq 0 ]; then
        echo -e "${RED}✗ $service_name runs as root (UID 0)${NC}"
        ((test_failed++))
        return 1
    elif [ "$uid" -eq "$expected_uid" ]; then
        echo -e "${GREEN}✓ $service_name runs as UID $uid (expected $expected_uid)${NC}"
        ((test_passed++))
        return 0
    else
        echo -e "${RED}✗ $service_name runs as UID $uid (expected $expected_uid)${NC}"
        ((test_failed++))
        return 1
    fi
}

# Function to test volume write access
test_volume_write() {
    local service_name=$1
    
    echo "Testing $service_name volume write access..."
    
    # Create a test volume
    local volume_name="animica-test-vol-$service_name"
    docker volume create "$volume_name" >/dev/null 2>&1
    
    # Test writing to the volume
    if docker run --rm -v "$volume_name:/data" "animica-$service_name:test" sh -c "touch /data/test.txt && echo 'success'" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ $service_name can write to volumes${NC}"
        ((test_passed++))
        result=0
    else
        echo -e "${RED}✗ $service_name cannot write to volumes${NC}"
        ((test_failed++))
        result=1
    fi
    
    # Clean up
    docker volume rm "$volume_name" >/dev/null 2>&1
    return $result
}

cd "$(dirname "$0")/../../.."

# Test node container
test_container_user "ops/docker/node.Dockerfile" 10001 "node"
test_volume_write "node"
echo

# Test miner container
test_container_user "ops/docker/miner.Dockerfile" 10002 "miner"
echo

# Test explorer container
test_container_user "ops/docker/explorer.Dockerfile" 10003 "explorer"
echo

# Test studio-services container
test_container_user "ops/docker/studio-services.Dockerfile" 10004 "studio-services"
echo

# Summary
echo "================================"
echo "Test Summary"
echo "================================"
echo -e "Passed: ${GREEN}$test_passed${NC}"
echo -e "Failed: ${RED}$test_failed${NC}"
echo

if [ $test_failed -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi
