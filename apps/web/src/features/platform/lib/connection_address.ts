/**
 * 연결 편집이 키를 다시 받아야 하는지 정하는 규칙과 허용 저장소 목록의 표기.
 *
 * 서버는 저장된 키가 있는 연결의 주소를 키 없이 바꾸면 422(`secret_required_for_address_change`)로 거절한다
 * (`services/stacks/connections.secret_required`). 화면이 같은 판정으로 키 입력란을 필수로 표시해야
 * 저장을 누른 뒤에야 거절을 보는 일이 없다. 두 런타임의 답은 판정 표
 * `packages/reborn-contracts/connection-address.json`을 양쪽 테스트가 읽어 맞춘다.
 */
import type { ConnectionProvider } from "@/shared/api/client";

/** 연결 종류별로 요청이 나가는 곳을 정하는 설정 키. 서버 `ROUTING_KEYS`와 같다. */
export const ROUTING_KEYS: Record<ConnectionProvider, string[]> = {
  s3: ["endpoint", "region"],
  ncp: ["endpoint", "region"],
  gcs: ["endpoint", "region"],
  maiu: ["base_url"],
  openai: ["base_url"],
  anthropic: ["base_url"],
  "local-llm": ["base_url"],
  bedrock: ["region"],
};

/** 주소 키. 비교할 때 끝 슬래시를 무시한다. */
const URL_KEYS = new Set(["base_url", "endpoint"]);

/** 비교에 쓰는 값. 빈 값과 없는 값은 같고, 앞뒤 공백과 주소 끝 슬래시는 무시한다. */
function routingValue(key: string, value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value).trim();
  return URL_KEYS.has(key) ? text.replace(/\/+$/, "") : text;
}

/** 두 설정 사이에서 값이 바뀐 주소 키를 돌려준다. 같은 값을 다시 보낸 키는 넣지 않는다. */
export function routingChanges(
  provider: ConnectionProvider,
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown>,
): string[] {
  const old = before ?? {};
  return ROUTING_KEYS[provider].filter((k) => routingValue(k, old[k]) !== routingValue(k, after[k]));
}

/**
 * 연결 수정이 키를 다시 받아야 하면 true를 돌려준다.
 * 저장된 키가 있고, 키를 넣지 않았고, 키를 지우지도 않는데 주소 키가 바뀌면 다시 받는다.
 */
export function secretRequired(p: {
  provider: ConnectionProvider;
  hasSecret: boolean;
  secretSent: boolean;
  clearSecret: boolean;
  before: Record<string, unknown> | null | undefined;
  after: Record<string, unknown>;
}): boolean {
  if (!p.hasSecret || p.secretSent || p.clearSecret) return false;
  return routingChanges(p.provider, p.before, p.after).length > 0;
}

/** 허용 저장소 입력란의 글자를 이름 목록으로 바꾼다. 쉼표·공백·줄바꿈으로 나누고 빈 항목은 뺀다. */
export function parseBucketList(text: string): string[] {
  return text.split(/[\s,]+/).filter((b) => b !== "");
}

/** 저장된 허용 저장소 목록을 입력란 글자로 바꾼다. 목록이 아니면 빈 글자다. */
export function bucketListText(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}
