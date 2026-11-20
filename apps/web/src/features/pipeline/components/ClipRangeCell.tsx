/**
 * 클립 구간 칩. 라벨링·검수의 '내 작업' 테이블이 함께 쓴다.
 *
 * - ClipRangeChip: 시작·끝 초를 이미 아는 경우에 쓴다. 조회 없이 칩만 렌더한다.
 * - ClipRangeCell: clip_id만 있는 경우(라벨링·검수 work_item)에 쓴다.
 *   클립을 조회한 뒤 ClipRangeChip을 보여 준다.
 * 칩 모양(필름 아이콘과 "start–end s" accent 칩)은 이 파일 한 곳에서만 정한다.
 */
import { useQuery } from "@tanstack/react-query";
import { Film } from "lucide-react";

import { fetchClip, libraryKeys } from "@/features/library";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { Skeleton } from "@/shared/ui/Skeleton";

/**
 * 목록 칩에 보여 줄 brief(AI 요약)의 최대 글자 수.
 * 전문을 모두 보여 주면 칩이 넘치므로 전문은 title로 보여 준다.
 */
const BRIEF_MAX = 45;

/**
 * 구간 칩을 그린다. 필름 아이콘과 "start–end s"(소수 1자리)를 보여 준다.
 * brief를 주면 뒤에 덧붙인다. 길면 …로 자르고 전문은 title에 둔다.
 */
export function ClipRangeChip({
  startSeconds,
  endSeconds,
  brief,
}: {
  startSeconds: number;
  endSeconds: number;
  brief?: string | null;
}) {
  const briefShort =
    brief && brief.length > BRIEF_MAX ? brief.slice(0, BRIEF_MAX).trimEnd() + "…" : brief;
  return (
    <span className="inline-flex items-center gap-1 rounded-control bg-accent-muted px-2 py-0.5 font-mono text-caption text-accent">
      <Film size={12} aria-hidden="true" />
      <span className="tabular-nums">
        {fmtClipRange(startSeconds, endSeconds)}
        {briefShort && (
          <span className="ml-1 text-label-2" title={brief ?? undefined}>· {briefShort}</span>
        )}
      </span>
    </span>
  );
}

/** clip_id로 클립을 조회해 ClipRangeChip으로 보여 준다. 시작·끝 초가 없는 work_item에 쓴다. */
export function ClipRangeCell({
  projectId,
  clipId,
  brief = false,
}: {
  projectId: string;
  clipId: string | null | undefined;
  /** true면 클립의 AI 요약(auto_caption_brief)을 구간 뒤에 덧붙인다. */
  brief?: boolean;
}) {
  const clip = useQuery({
    queryKey: libraryKeys.clip(projectId, clipId),
    queryFn: () => fetchClip(projectId, clipId),
    enabled: !!clipId,
    staleTime: 60_000,
  });

  if (!clipId) return <span className="text-caption text-label-3">영상 단위 작업</span>;
  if (clip.isLoading) {
    return (
      <span className="inline-flex items-center gap-1 rounded-control bg-accent-muted px-2 py-0.5 text-accent">
        <Film size={12} aria-hidden="true" />
        <Skeleton className="inline-block h-3 w-20" />
      </span>
    );
  }
  if (!clip.data) {
    return (
      <span className="inline-flex items-center gap-1 rounded-control bg-accent-muted px-2 py-0.5 font-mono text-caption text-accent">
        <Film size={12} aria-hidden="true" />
        <span className="text-label-3">클립</span>
      </span>
    );
  }
  return (
    <ClipRangeChip
      startSeconds={clip.data.start_seconds}
      endSeconds={clip.data.end_seconds}
      brief={brief ? clip.data.auto_caption_brief : undefined}
    />
  );
}
