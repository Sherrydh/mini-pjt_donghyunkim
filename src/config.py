"""공통 설정: 경로, LLM, 임베딩. (src/reference/react_agent.py 의 Bedrock 설정을 재사용)"""
import os
from pathlib import Path

from botocore.config import Config
from dotenv import load_dotenv
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CHROMA_DIR = ROOT / "chroma_db"

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
LLM_MODEL = os.getenv("LLM_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
EMBED_MODEL = os.getenv("EMBED_MODEL", "amazon.titan-embed-text-v2:0")

# Bedrock ThrottlingException 대비 적응형 재시도
_boto_config = Config(retries={"mode": "adaptive", "max_attempts": 10})

llm = ChatBedrockConverse(model=LLM_MODEL, region_name=REGION, temperature=0, config=_boto_config)
embeddings = BedrockEmbeddings(model_id=EMBED_MODEL, region_name=REGION, config=_boto_config)


def get_text(message) -> str:
    """ChatBedrockConverse는 content를 블록 리스트로 주기도 하므로 텍스트만 모아 반환합니다."""
    content = message.content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return content or ""
