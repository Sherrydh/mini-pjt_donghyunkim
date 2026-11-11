"""공통 설정: 경로, Bedrock LLM·임베딩, LangSmith 트레이싱 플래그."""
import os
from pathlib import Path

from botocore.config import Config
from dotenv import load_dotenv
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
REPERTOIRE_PATH = DATA_DIR / "repertoire.json"
CHROMA_DIR = ROOT / "chroma_db"

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
LLM_MODEL = os.getenv("LLM_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
EMBED_MODEL = os.getenv("EMBED_MODEL", "amazon.titan-embed-text-v2:0")

# LangSmith: .env 에 LANGSMITH_API_KEY 가 있고 LANGSMITH_TRACING=true 면 LangChain이 자동으로 트레이스를 보낸다.
LANGSMITH_ENABLED = bool(os.getenv("LANGSMITH_API_KEY")) and os.getenv("LANGSMITH_TRACING", "").lower() == "true"

# Bedrock ThrottlingException 대비 적응형 재시도 (미들웨어: 재시도)
_boto_config = Config(retries={"mode": "adaptive", "max_attempts": 10})

llm = ChatBedrockConverse(model=LLM_MODEL, region_name=REGION, temperature=0, config=_boto_config)
embeddings = BedrockEmbeddings(model_id=EMBED_MODEL, region_name=REGION, config=_boto_config)


def get_text(message) -> str:
    """ChatBedrockConverse는 content를 블록 리스트로 주기도 하므로 텍스트만 모아 반환한다."""
    content = message.content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return content or ""
