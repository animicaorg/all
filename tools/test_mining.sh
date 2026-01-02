#!/usr/bin/env bash
set -euo pipefail

pytest -q mining/tests -k mining
pytest -q consensus/tests -k scorer_accept_reject
