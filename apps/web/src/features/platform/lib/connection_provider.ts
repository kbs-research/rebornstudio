/**
 * 자격증명 연결의 서비스 이름과 묶음. 연결 화면과 플랫폼 개요가 같이 쓴다.
 *
 * 두 화면이 같은 서비스를 다른 이름으로 부르지 않게 한 곳에 둔다.
 */
import type { ConnectionProvider } from "@/shared/api/client";

/** 서비스의 화면 이름. 분석 서비스는 화면 어디서나 `Media Intelligence`로 부른다. */
export const PROVIDER_LABEL: Record<ConnectionProvider, string> = {
  s3: "AWS S3",
  ncp: "NCP Object Storage",
  gcs: "Google Cloud Storage",
  maiu: "Media Intelligence",
  openai: "OpenAI",
  anthropic: "Anthropic",
  // 사내·사설 모델(vLLM, Ollama 등). OpenAI 호환 API 하나로 받는다.
  "local-llm": "사내 모델",
  bedrock: "AWS Bedrock",
};

/**
 * 캡션 품질검수가 읽는 모델 서비스 목록.
 * 저장소·분석 서비스 연결은 프로젝트가 고르면 업로드와 분석이 읽는다(`services/stacks/connections.py` 머리 주석).
 */
export const MODEL_PROVIDERS: ConnectionProvider[] = ["openai", "anthropic", "local-llm", "bedrock"];

/** 캡션 품질검수 모델 서비스이면 true. */
export const isModelProvider = (p: ConnectionProvider): boolean => MODEL_PROVIDERS.includes(p);

/** 서비스 묶음 이름을 돌려준다. 연결 화면의 묶음 머리와 개요의 출처 줄이 같은 이름을 쓴다. */
export const providerGroupLabel = (p: ConnectionProvider): string =>
  isModelProvider(p) ? "캡션 품질검수 모델" : "저장소·분석 서비스";
