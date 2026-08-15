#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

tailwindcss \
    --input tailwind.css \
    --output static/all-tailwind-classes-full-min.css \
    --minify
