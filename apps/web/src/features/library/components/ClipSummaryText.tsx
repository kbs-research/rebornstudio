/**
 * 클립 한 줄 요약 부품. 고르는 규칙은 `clip_summary.ts`에 있고, 이 부품은 모양만 정한다.
 *
 * 음성 인식 문장으로 대신할 때는 앞에 작은 라벨을 붙인다. 라벨이 없으면 발화 문장이 장면 요약처럼 읽힌다.
 */
import { clipSummary, SPEECH_SOURCE_LABEL } from "../lib/clip_summary";

/** 클립 한 줄 요약을 그린다. 음성 인식 문장이면 출처 라벨을 앞에 붙인다. */
export function ClipSummaryText({
  clip,
}: {
  clip: { scene_summary?: string | null; auto_caption_brief?: string | null };
}) {
  const s = clipSummary(clip);
  if (!s) return <span className="text-label-3">요약 없음</span>;
  return (
    <>
      {s.source === "speech" && (
        <span className="mr-1.5 inline-block rounded-mark bg-canvas-2 px-1.5 align-[1px] text-micro font-semibold text-label-2">
          {SPEECH_SOURCE_LABEL}
        </span>
      )}
      {s.text}
    </>
  );
}
