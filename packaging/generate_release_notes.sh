#!/bin/bash
# Writes /tmp/release-notes.md: a "## What's changed" commit log between the
# previous tag and the one being released, same format as the inline
# version in .github/workflows/appimage.yml. Factored out here so the
# Windows/macOS jobs in windows-macos-build.yml can reuse it without a
# third copy-pasted inline block -- appimage.yml keeps its own inline copy
# as-is (this project's plan intentionally limits that file's changes to a
# single added smoke-test step, so this refactor doesn't reach into it).
#
# Requires GITHUB_REF_NAME and GITHUB_REPOSITORY (both set automatically in
# GitHub Actions) and a full-history checkout (actions/checkout with
# fetch-depth: 0).

set -euo pipefail

TAG="${GITHUB_REF_NAME}"
PREV_TAG="$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)"

{
    echo "## What's changed"
    echo
    if [ -n "${PREV_TAG}" ]; then
        git log --oneline --no-decorate "${PREV_TAG}..${TAG}"
    else
        git log --oneline --no-decorate "${TAG}"
    fi | sed 's/^/- /'
    if [ -n "${PREV_TAG}" ]; then
        echo
        echo "**Full Changelog**: https://github.com/${GITHUB_REPOSITORY}/compare/${PREV_TAG}...${TAG}"
    fi
} > /tmp/release-notes.md
