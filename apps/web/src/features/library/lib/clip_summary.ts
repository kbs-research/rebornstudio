/**
 * 클립 한 줄 요약을 고르는 규칙. 영상 상세의 클립 목록과 클립 보기가 같은 규칙을 쓴다.
 *
 * 장면 요약을 먼저 쓴다. 장면 요약이 없으면 음성 인식 문장을 쓰고, 그때는 출처를 함께 돌려준다.
 * 화면은 출처가 음성 인식이면 작은 라벨을 붙여 장면 요약과 구분한다.
 * 두 화면이 서로 다른 필드를 읽으면 같은 클립이 한 화면에서는 `장면 요약 없음`, 다른 화면에서는 장면 요약으로 보인다.
 */

/** 요약 한 줄과 그 출처. */
export interface ClipSummary {
  text: string;
  /** `scene`은 장면 요약, `speech`는 음성 인식 문장. */
  source: "scene" | "speech";
}

/** 출처가 장면 요약이 아닐 때 붙이는 라벨. 라벨링 화면이 같은 내용을 `음성 인식`이라 부른다. */
export const SPEECH_SOURCE_LABEL = "음성 인식";

/**
 * 클립에서 보여 줄 한 줄을 고른다. 공백뿐인 값은 없는 것으로 본다.
 *
 * Returns:
 *   고를 값이 없으면 `null`이다. 화면은 그때 `요약 없음`을 쓴다.
 */
export function clipSummary(c: {
  scene_summary?: string | null;
  auto_caption_brief?: string | null;
}): ClipSummary | null {
  const scene = c.scene_summary?.trim();
  if (scene) return { text: scene, source: "scene" };
  const speech = c.auto_caption_brief?.trim();
  if (speech) return { text: speech, source: "speech" };
  return null;
}
