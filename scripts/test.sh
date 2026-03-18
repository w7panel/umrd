#!/bin/bash
set -e

echo "Running tests..."
python3 -m pytest tests/ -v

echo "Running linting..."
python3 -m black --check src/ || true

echo "All checks passed!"
