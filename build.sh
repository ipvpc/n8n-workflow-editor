#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/frontend"
npm install
npm run build
