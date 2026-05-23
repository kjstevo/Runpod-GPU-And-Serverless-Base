#!/bin/bash

WORKSPACE_DIR="${WORKSPACE_DIR:-/app}"
REPO_OWNER="${REPO_OWNER:-kjstevo}"
REPO_NAME="${REPO_NAME:-Runpod-GPU-And-Serverless-Base}"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_RAW="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"

echo "--- Pulling latest files from ${REPO_RAW} ---"

for file in handler.py start.sh pull_changes.py; do
    if curl -fsSL --max-time 30 "${REPO_RAW}/${file}" -o "${WORKSPACE_DIR}/${file}.tmp" 2>/dev/null; then
        mv "${WORKSPACE_DIR}/${file}.tmp" "${WORKSPACE_DIR}/${file}"
        echo "  Updated: ${file}"
    else
        echo "  Warning: Could not fetch ${file}, using baked-in version"
        rm -f "${WORKSPACE_DIR}/${file}.tmp"
    fi
done

echo "--- Running pull_changes.py ---"
python3 "${WORKSPACE_DIR}/pull_changes.py"

CHANGES_DIR="${WORKSPACE_DIR}/karaoke_gen_changes/karaoke_gen"
if [ -d "${CHANGES_DIR}" ]; then
    KARAOKE_GEN_DIR=$(python3 -c "import karaoke_gen, os; print(os.path.dirname(karaoke_gen.__file__))" 2>/dev/null)
    if [ -n "${KARAOKE_GEN_DIR}" ]; then
        echo "--- Copying karaoke_gen_changes to ${KARAOKE_GEN_DIR} ---"
        cp -R "${CHANGES_DIR}/"* "${KARAOKE_GEN_DIR}/"
    else
        echo "  Warning: Could not locate karaoke_gen install directory, skipping patch"
    fi
fi

chmod +x "${WORKSPACE_DIR}/start.sh"
echo "--- Handing off to start.sh ---"
exec "${WORKSPACE_DIR}/start.sh"
