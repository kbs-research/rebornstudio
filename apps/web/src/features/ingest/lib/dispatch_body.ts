/**
 * 분석 요청 본문. 대상 영상만 싣는다.
 *
 * 클립 길이는 모든 프로젝트가 같은 기본값을 써서 요청마다 고르지 않는다.
 * 서버는 모르는 필드를 422로 거절하므로 계약(`MaiuDispatchRequest`)에 있는 필드만 싣는다.
 */
import type { components } from "@reborn/contracts";

/** 분석 요청 본문. 일괄 요청 수(`limit`)는 서버 기본값을 쓰므로 싣지 않는다. */
export type AnalysisDispatchBody = Pick<components["schemas"]["MaiuDispatchRequest"], "video_ids">;

/** 고른 영상의 분석 요청 본문을 만든다. */
export function dispatchBody(videoIds: readonly string[]): AnalysisDispatchBody {
  return { video_ids: [...videoIds] };
}
