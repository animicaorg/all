#!/usr/bin/env bash
# Shared utility to ensure .env file exists
# Source this script from other scripts

ensure_env_file() {
  local root_dir="${1:?Root directory required}"
  local env_file="$root_dir/env/.env"
  local env_example="$root_dir/env/.env.example"

  if [[ ! -f "$env_file" ]]; then
    if [[ -f "$env_example" ]]; then
      echo "⚠️  .env file not found. Creating from .env.example..."
      cp "$env_example" "$env_file"
      echo "✅ Created $env_file"
      echo "📝 Please review and update the environment variables if needed."
      echo ""
    else
      echo "❌ Error: Neither $env_file nor $env_example found."
      echo "Please create $env_file with required database configuration."
      exit 1
    fi
  fi
}
