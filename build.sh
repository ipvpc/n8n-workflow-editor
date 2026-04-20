#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/frontend"
npm ci
npm run build
