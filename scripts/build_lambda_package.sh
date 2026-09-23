#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$project_root/.lambda-build"
output_dir="$project_root/dist"

rm -rf "$build_dir"
mkdir -p "$build_dir" "$output_dir"
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --target "$build_dir" "pydantic>=2.9"
else
  python -m pip install --quiet --target "$build_dir" "pydantic>=2.9"
fi
cp -R "$project_root/riskqueue" "$build_dir/riskqueue"
(
  cd "$build_dir"
  zip -qr "$output_dir/riskqueue-ingestion-lambda.zip" .
)
echo "Created $output_dir/riskqueue-ingestion-lambda.zip"
