#!/usr/bin/env bash
# Downloads a small Vosk model for offline keyword spotting.
#
# Usage:
#   bash scripts/download_vosk_model.sh        # English (default)
#   bash scripts/download_vosk_model.sh en
#   bash scripts/download_vosk_model.sh de      # German
set -euo pipefail

LANGUAGE="${1:-en}"

case "$LANGUAGE" in
  en) MODEL_NAME="vosk-model-small-en-us-0.15" ;;
  de) MODEL_NAME="vosk-model-small-de-0.15" ;;
  *)
    echo "Unknown language '$LANGUAGE'. Supported: en, de" >&2
    exit 1
    ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$REPO_ROOT/assets/models"
URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"

mkdir -p "$MODELS_DIR"
cd "$MODELS_DIR"

if [ -d "$MODEL_NAME" ]; then
  echo "Model already present at $MODELS_DIR/$MODEL_NAME"
  exit 0
fi

echo "Downloading $URL ..."
wget -q --show-progress -O "${MODEL_NAME}.zip" "$URL"

echo "Unzipping..."
unzip -q "${MODEL_NAME}.zip"
rm "${MODEL_NAME}.zip"

echo "Model ready at $MODELS_DIR/$MODEL_NAME"
