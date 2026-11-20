/**
 * `내 작업` 목록의 클립 한 줄 아래에 보여 줄 캡션 미리보기를 고른다.
 *
 * 라벨러가 쓴 캡션이 있으면 라벨러 캡션을 쓴다. 임시저장(`draft`)이 먼저이고, 없으면 작업 행의 최신 제출
 * 스냅숏(`payload`)이다. 검수 목록에서는 이 스냅숏이 검수할 제출 캡션이고, 재작업 행에서는 직전 제출이다.
 * 라벨러 캡션이 없을 때만 분석 결과(장면 요약, 없으면 음성 인식)로 대신하고 출처를 함께 돌려준다.
 * 화면은 그때 출처 라벨을 붙인다. 붙이지 않으면 분석 원문이 라벨러 캡션처럼 읽혀
 * 검수자는 검수할 글을, 라벨러는 자기가 고친 글을 목록에서 알아볼 수 없다.
 * 컴포넌트 밖에 두어 이 판정만 따로 테스트한다.
 */
import { clipSummary } from "@/features/library";
import { finalText } from "@/features/workbench";

/** 미리보기 한 줄과 그 출처. */
export interface QueuePreview {
  text: string;
  /** `caption`은 라벨러 캡션, `scene`은 장면 요약, `speech`는 음성 인식 문장. */
  source: "caption" | "scene" | "speech";
}

/**
 * 작업과 클립에서 미리보기 한 줄을 고른다.
 *
 * Args:
 *   work: 작업 행의 임시저장(`draft`)과 최신 제출 스냅숏(`payload`).
 *   clip: 클립의 분석 결과.
 *
 * Returns:
 *   라벨러 캡션, 장면 요약, 음성 인식 순으로 처음 있는 것. 모두 없으면 `null`.
 */
export function queuePreview(
  work: { draft?: unknown; payload?: unknown } | undefined,
  clip: { scene_summary?: string | null; auto_caption_brief?: string | null } | undefined,
): QueuePreview | null {
  const caption = finalText(work?.draft) || finalText(work?.payload);
  if (caption) return { text: caption, source: "caption" };
  const s = clip ? clipSummary(clip) : null;
  return s ? { text: s.text, source: s.source } : null;
}
