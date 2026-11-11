#!/usr/bin/env bash
# 사용법: ./run.sh   (사전: pip install -r requirements.txt, .env 에 AWS 자격증명)
set -e
cd "$(dirname "$0")"
uvicorn src.api:app --host 0.0.0.0 --port "${PORT:-8000}"
