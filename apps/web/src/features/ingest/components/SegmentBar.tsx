/**
 * 준비 분포 막대.
 *
 * 규칙:
 *  1. 완료가 왼쪽부터 채워진다.
 *  2. 1건짜리 조각에도 최소 폭이 있다. 300건 Job의 이슈 1건이 0px가 되면 막대가 문제가 없는 것처럼 보인다.
 *     비율은 최소 폭을 뺀 나머지 폭에서 나눈다.
 *  3. 보이는 조각을 누르면 그 목록으로 간다.
 *
 * 막대만으로는 1건을 읽을 수 없어서 호출하는 쪽이 숫자를 함께 적는다.
 */
import { cn } from "@/shared/lib/cn";

/** 분포 조각 하나. */
export type Segment = {
  key: string;
  label: string;
  n: number;
  /** `TONE_FILL` 값. 색은 토큰만 쓴다. */
  fill: string;
};

/** 조각 하나가 차지하는 최소 폭(px). 이보다 얇으면 보이지 않는다. */
const MIN_PX = 8;

/** 분포 막대를 그린다. 조각을 누르면 `onPick`으로 그 키를 넘긴다. */
export function SegmentBar({
  segments, total, onPick, className, width = 176,
}: {
  segments: Segment[];
  /** 전체 수. 조각 합과 다르면 나머지는 빈 트랙으로 남긴다. 조각을 늘려 채우지 않는다. */
  total: number;
  onPick?: (key: string) => void;
  className?: string;
  width?: number;
}) {
  const shown = segments.filter((s) => s.n > 0);
  if (!shown.length || total <= 0) return null;

  // 최소 폭을 먼저 떼고 남은 폭을 비율로 나눈다. 그래서 작은 조각도 보인다.
  const reserved = shown.length * MIN_PX;
  const flexible = Math.max(0, width - reserved);
  const sum = shown.reduce((s, x) => s + x.n, 0);

  return (
    <span
      className={cn("flex h-1.5 overflow-hidden rounded-capsule bg-fill", className)}
      style={{ width }}
    >
      {shown.map((s) => {
        const px = MIN_PX + (flexible * s.n) / Math.max(sum, 1);
        // 영상 수라 단위는 `개`다.
        const title = `${s.label} ${s.n.toLocaleString("ko-KR")}개 / 전체 ${total.toLocaleString("ko-KR")}개`;
        return onPick ? (
          <button
            key={s.key}
            type="button"
            title={title}
            aria-label={title}
            onClick={(e) => { e.stopPropagation(); onPick(s.key); }}
            className={cn("transition-opacity hover:opacity-70", s.fill)}
            style={{ width: px }}
          />
        ) : (
          <span key={s.key} className={s.fill} style={{ width: px }} title={title} aria-hidden="true" />
        );
      })}
    </span>
  );
}
