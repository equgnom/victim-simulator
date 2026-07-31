#!/usr/bin/env bash
# Downloads the small English Vosk model (~40MB) used for offline keyword
# spotting. Same model works on the dev laptop and on the Pi 4B.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$REPO_ROOT/assets/models"
MODEL_NAME="vosk-model-small-en-us-0.15"
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
