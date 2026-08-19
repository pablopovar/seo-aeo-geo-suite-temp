#!/usr/bin/env bash
set -euo pipefail

bad="$(
  find . \
    -path './.git' -prune -o \
    -type f \( \
      -name '*.tar' -o \
      -name '*.tar.gz' -o \
      -name '*.tgz' -o \
      -name '*.tar.bz2' -o \
      -name '*.tar.xz' -o \
      -name '*.zip' -o \
      -name '*.7z' -o \
      -name '*.patch' -o \
      -name '*.diff' -o \
      -name '*.bak' -o \
      -name '*.bak-*' -o \
      -name '*.backup' -o \
      -name '*.orig' -o \
      -name '*.rej' -o \
      -name '*.PBAK' -o \
      -name '*.PBAK.*' \
    \) -print
)"

bad_dirs="$(
  find . \
    -path './.git' -prune -o \
    -type d \( \
      -name 'patch' -o \
      -name 'patches' -o \
      -name '*-patch' -o \
      -name '*-patches' \
    \) -print
)"

if [[ -n "$bad" || -n "$bad_dirs" ]]; then
  echo "ERROR: patch/build/backup artifacts found inside repository:"
  [[ -n "$bad" ]] && printf '%s\n' "$bad"
  [[ -n "$bad_dirs" ]] && printf '%s\n' "$bad_dirs"
  echo
  echo "Move them to:"
  echo "/home/pablo/ownCloud/Projects/ai-systems-observer/ai-observer/projects/aeo-geo-seo-auditor/patches/"
  exit 1
fi

echo "OK: no patch/build/backup artifacts found inside repository."
