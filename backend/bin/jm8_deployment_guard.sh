#!/usr/bin/env bash
# JM8 Environment Contract Shell Wrapper
#
# Source this file at the start of mutating deployment scripts to validate
# the environment contract before making any AWS changes.
#
# Usage in a bash script:
#   #!/usr/bin/env bash
#   set -euo pipefail
#
#   # Source the contract guard
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/jm8_deployment_guard.sh"
#
#   # Validate environment before proceeding
#   jm8_validate_contract_or_exit
#
#   # Continue with deployment logic...
#

function jm8_validate_contract_or_exit() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"

    local python_guard
    python_guard="$script_dir/jm8_environment_contract.py"

    if [[ ! -f "$python_guard" ]]; then
        echo "ERROR: jm8_environment_contract.py not found at $python_guard" >&2
        exit 1
    fi

    # Run the Python validator
    if ! python3 "$python_guard" validate; then
        # Python will output the error to stderr; exit with error code
        exit 1
    fi
}

export -f jm8_validate_contract_or_exit
