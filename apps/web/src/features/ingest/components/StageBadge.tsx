/**
 * 준비 단계 상태 배지.
 *
 * 라벨 앞에 점을 붙인다(감사 로그의 심각도 점과 같은 방식). 목록을 훑을 때 글자를 읽지 않고도 빨간 점이 몇 개인지 보인다.
 *
 * 판정은 서버가 하고, 문구와 색은 `vocab` 한 곳에서 정한다.
 */
import { Chip } from "@/shared/ui/Chip";
import { STAGE_LABEL, STAGE_TONE, TONE_CHIP_TONE, type PrepStage } from "../lib/vocab";

/** 준비 단계 값을 점과 라벨이 있는 상태 칩으로 그린다. 모양은 공용 점 칩이다. */
export function StageBadge({ stage, className }: { stage: PrepStage; className?: string }) {
  return (
    <Chip variant="dot" tone={TONE_CHIP_TONE[STAGE_TONE[stage]]} className={className}>
      {STAGE_LABEL[stage]}
    </Chip>
  );
}
