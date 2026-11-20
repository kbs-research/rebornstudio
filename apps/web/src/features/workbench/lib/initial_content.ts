/**
 * 라벨링 워크벤치를 열 때 캡션을 무엇으로 채울지 정한다.
 *
 * 작업·라벨 이력·클립은 따로 조회되어 도착 순서가 정해져 있지 않다.
 * 판단을 훅 밖의 순수 함수로 두어 도착 순서마다 결과를 테스트한다.
 */

/** 캡션을 채우는 방법. */
export type InitialContent<T> =
  | { kind: "restore"; src: T }
  | { kind: "seed"; text: string }
  | { kind: "failed" }
  | { kind: "none" };

/** 판단에 쓰는 입력. */
export interface InitialContentInput<T> {
  /** 작업에 임시저장된 초안. */
  draft?: T | null;
  /** 라벨 이력. `undefined`면 아직 도착하지 않았다는 뜻이다. */
  labels?: ReadonlyArray<{ label_data?: T | null }>;
  /** 라벨 이력 조회에 실패했으면 true. */
  labelsFailed?: boolean;
  /** 클립의 AI 장면 요약. */
  aiSummary?: string | null;
}

/**
 * 임시저장 초안, 마지막 제출 라벨, AI 초안 순으로 캡션의 출처를 고른다.
 *
 * AI 초안은 라벨 이력이 도착해 저장된 라벨이 없다고 확정된 뒤에만 고른다.
 * 클립이 이력보다 먼저 도착했을 때 AI 초안을 먼저 채우면, 뒤에 온 라벨로 캡션이
 * 복원되어도 AI 초안 안내와 교정 표시가 남는다.
 *
 * 초안이 없는데 라벨 이력 조회가 실패하면 `failed`를 돌려준다. 저장된 라벨이 있는지 확인할 수 없으므로
 * AI 초안을 채우지 않는다. 화면은 빈 캡션 대신 조회 실패를 표시한다.
 *
 * @param x 작업 초안·라벨 이력·이력 조회 실패 여부·AI 장면 요약.
 * @returns 복원할 내용, 채울 AI 초안, 이력 조회 실패, 할 일 없음 중 하나의 결정.
 */
export function pickInitialContent<T>(x: InitialContentInput<T>): InitialContent<T> {
  const src = x.draft ?? x.labels?.[x.labels.length - 1]?.label_data;
  if (src) return { kind: "restore", src };
  if (x.labels === undefined) return x.labelsFailed ? { kind: "failed" } : { kind: "none" };
  const ai = x.aiSummary?.trim() ?? "";
  if (ai) return { kind: "seed", text: ai };
  return { kind: "none" };
}

/**
 * 작업 캐시 값에 마지막으로 저장한 초안을 싣는다. 캐시에 작업이 없으면 `undefined`를 그대로 돌려준다.
 *
 * 라벨링 화면은 작업마다 새로 마운트하고, 새 화면은 캐시에 남은 작업의 초안을 바로 복원한다.
 * 임시저장은 캐시를 고치지 않는다. 떠날 때 저장한 초안을 싣지 않으면 캐시가 유효한 동안 돌아온 화면이 이전 초안을 복원한다.
 * 그러면 그 위에 이어 쓴 편집이 저장한 초안을 덮는다.
 */
export function withSavedDraft<T extends { draft?: unknown }>(item: T | undefined, body: object): T | undefined {
  return item && { ...item, draft: body };
}
