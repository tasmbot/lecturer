#!/bin/bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON=""

echo "======================================"
echo " Lecturer setup"
echo "======================================"
echo

# --------------------------------------------------
# Find Python
# --------------------------------------------------

if command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif [ -x "/opt/homebrew/bin/python3" ]; then
    PYTHON="/opt/homebrew/bin/python3"
elif [ -x "/usr/local/bin/python3" ]; then
    PYTHON="/usr/local/bin/python3"
else
    echo "ERROR: Python 3 was not found."
    echo
    echo "Install Python first, for example with Homebrew:"
    echo "  brew install python"
    exit 1
fi

echo "Using Python:"
echo "  $PYTHON"
echo

# --------------------------------------------------
# Check Python version
# --------------------------------------------------

PYTHON_VERSION="$("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

echo "Python version: $PYTHON_VERSION"

"$PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 10):
    print("ERROR: Python 3.10 or newer is required.")
    sys.exit(1)
PY

echo "✓ Python version is supported"
echo

# --------------------------------------------------
# Create virtual environment
# --------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."

    "$PYTHON" -m venv "$VENV_DIR"

    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

# --------------------------------------------------
# Upgrade pip
# --------------------------------------------------

echo
echo "Updating pip..."

"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel

echo "✓ pip updated"

# --------------------------------------------------
# Install dependencies
# --------------------------------------------------

if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo
    echo "Installing dependencies..."

    "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"

    echo "✓ Dependencies installed"
else
    echo
    echo "WARNING: requirements.txt was not found."
fi

# --------------------------------------------------
# Install lecturer itself
# --------------------------------------------------

if [ -f "$PROJECT_DIR/pyproject.toml" ]; then
    echo
    echo "Installing lecturer..."

    "$VENV_PYTHON" -m pip install -e "$PROJECT_DIR"

    echo "✓ Lecturer installed"
else
    echo
    echo "No pyproject.toml found."
    echo "Lecturer will be launched directly from the repository."
fi

# --------------------------------------------------
# Create .env
# --------------------------------------------------

if [ -f "$PROJECT_DIR/.env.example" ]; then
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo
        echo "✓ Created .env from .env.example"
        echo
        echo "IMPORTANT: edit .env and add your configuration."
    else
        echo
        echo "✓ .env already exists"
    fi
fi

# --------------------------------------------------
# Make launcher executable
# --------------------------------------------------

if [ -f "$PROJECT_DIR/bin/lecturer" ]; then
    chmod +x "$PROJECT_DIR/bin/lecturer"
    echo
    echo "✓ Launcher is executable"
fi

# --------------------------------------------------
# Check external dependencies
# --------------------------------------------------

echo
echo "Checking external dependencies..."

if command -v ollama >/dev/null 2>&1; then
    echo "✓ Ollama found: $(command -v ollama)"
else
    echo "⚠ Ollama was not found."
    echo "  Install Ollama separately before using summarization."
fi

if system_profiler SPUSBDataType 2>/dev/null | grep -qi "BlackHole"; then
    echo "✓ BlackHole appears to be installed"
else
    echo "⚠ BlackHole was not detected."
    echo "  Make sure BlackHole 2ch is installed and configured."
fi

# --------------------------------------------------
# Finish
# --------------------------------------------------

echo
echo "======================================"
echo " Setup complete"
echo "======================================"
echo
echo "Python:"
echo "  $VENV_PYTHON"
echo
echo "Start:"
echo "  ./bin/lecturer start --source both"
echo
echo "Stop:"
echo "  ./bin/lecturer stop"
echo