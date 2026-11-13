#!/usr/bin/env bash
# 피아노 독학 길잡이 실행 스크립트
#   bash run.sh          서버 실행 (http://localhost:8000 웹 화면 · POST /query)
#   bash run.sh eval     인-아웃 세트 평가 (evaluation/results_round2_<tag>.json)
#   bash run.sh multi    대화형(멀티턴) 시나리오 평가
#   bash run.sh test     pytest
set -euo pipefail
cd "$(dirname "$0")"

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe   # Windows
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python                 # macOS/Linux
else PY=python; fi

[ -f .env ] || echo "경고: .env 가 없습니다. AWS 자격 증명을 README '실행 방법 2' 대로 설정하세요." >&2

case "${1:-serve}" in
  serve) exec "$PY" -m uvicorn src.api:app --host 127.0.0.1 --port "${PORT:-8000}" ;;
  eval)  exec "$PY" -m evaluation.run_eval --round 2 --tag "${2:-local}" ;;
  multi) exec "$PY" -m evaluation.run_multiturn ;;
  test)  exec "$PY" -m pytest -q ;;
  *) echo "사용법: bash run.sh [serve|eval [tag]|multi|test]" >&2; exit 1 ;;
esac
