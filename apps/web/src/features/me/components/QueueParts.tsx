/**
 * `내 작업`의 두 대기열(라벨링·검수)이 보유 작업 미리보기에 함께 쓰는 작은 부품.
 *
 * 클립 한 줄과 반려 사유를 이 파일에 둔다. 역할이 바뀌어도 같은 일은 같은 모양으로 보여야 한다.
 */
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@reborn/contracts";

import { Chip } from "@/shared/ui/Chip";
import { Skeleton } from "@/shared/ui/Skeleton";
import { fetchClip, libraryKeys, SPEECH_SOURCE_LABEL } from "@/features/library";
import { fmtClipRange } from "@/shared/lib/fmtSecs";

import { queuePreview, type QueuePreview } from "../lib/queue_preview";

/** 분석 결과로 대신할 때 앞에 붙이는 출처 라벨. 라벨러 캡션에는 붙이지 않는다. */
const SOURCE_LABEL: Record<Exclude<QueuePreview["source"], "caption">, string> = {
  scene: "장면 요약",
  speech: SPEECH_SOURCE_LABEL,
};

/** 미리보기 문장을 그린다. 분석 결과로 대신한 것이면 출처 라벨을 앞에 붙인다. */
function PreviewText({ preview }: { preview: QueuePreview }) {
  return (
    <>
      {preview.source !== "caption" && (
        <span className="mr-1.5 inline-block rounded-mark bg-canvas-2 px-1.5 align-[1px] text-micro font-semibold text-label-2">
          {SOURCE_LABEL[preview.source]}
        </span>
      )}
      {preview.text}
    </>
  );
}

/**
 * 클립 한 줄을 그린다. 첫 줄은 영상 제목과 구간, 둘째 줄은 캡션 미리보기다.
 *
 * 작업 ID 앞자리로는 어느 장면인지 떠올릴 수 없어 `내 작업` 표에 ID를 두지 않는다. 영상 제목과 미리보기로 작업을 알아본다.
 * 미리보기는 라벨러 캡션(임시저장, 없으면 최신 제출)이고, 없을 때만 장면 요약·음성 인식으로 대신하며 출처를 붙인다
 * (`lib/queue_preview`). 모두 없으면 첫 줄만 쓴다.
 * 조회 키는 작업 단계 화면의 클립 열(`ui/ClipRangeCell`)과 같아 요청이 늘지 않는다.
 */
export function ClipLine({
  projectId,
  clipId,
  work,
  size = "md",
  extra,
}: {
  projectId: string;
  clipId: string | null | undefined;
  /** 작업 행의 임시저장과 최신 제출 스냅숏. 있으면 라벨러 캡션을 미리보기로 쓴다. */
  work?: { draft?: unknown; payload?: unknown };
  /** 글자 크기. `lg`는 다음 작업 카드의 제목 크기다. */
  size?: "md" | "lg";
  /** 구간 옆에 붙는 표시(재작업 칩 등) */
  extra?: ReactNode;
}) {
  const clip = useQuery({
    queryKey: libraryKeys.clip(projectId, clipId),
    queryFn: () => fetchClip(projectId, clipId),
    enabled: !!clipId,
    staleTime: 60_000,
  });
  if (!clipId) return <span className="text-footnote text-label-3">영상 단위 작업</span>;
  if (clip.isLoading) return <Skeleton className="h-4 w-40" />;
  const range = clip.data ? fmtClipRange(clip.data.start_seconds, clip.data.end_seconds) : "클립";
  const preview = queuePreview(work, clip.data);
  const title = clip.data?.video_title;
  // 첫 줄은 두 크기가 같다. 영상 제목은 길면 자르고 구간은 자르지 않는다.
  const head = (
    <span className="flex min-w-0 items-center gap-2 text-footnote text-label-2">
      {title && (
        <>
          <span className="min-w-0 truncate" title={title}>{title}</span>
          <span aria-hidden="true" className="text-label-3">·</span>
        </>
      )}
      <span className="shrink-0 font-mono tabular-nums">{range}</span>
      {extra && <span className="shrink-0">{extra}</span>}
    </span>
  );
  if (size === "lg") {
    return (
      <span className="block min-w-0 space-y-0.5">
        {head}
        {preview && (
          <span className="block truncate text-headline text-label" title={preview.text}>
            <PreviewText preview={preview} />
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="block min-w-0">
      {head}
      {preview && (
        <span className="mt-0.5 block max-w-[40rem] truncate text-footnote text-label" title={preview.text}>
          <PreviewText preview={preview} />
        </span>
      )}
    </span>
  );
}

/**
 * 재작업의 마지막 반려 사유를 칩으로 그린다. 사유가 없으면 아무것도 그리지 않는다.
 *
 * 사유 이름은 서버가 프로젝트 사유 목록에서 찾아 준다(`last_reject_reasons`). 검수 결과 화면과 같은 칩이다.
 * 이름표 `반려 사유`를 앞에 두어 재작업 칩과 한 줄에 있어도 무엇인지 읽힌다.
 */
export function RejectReasons({ reasons }: { reasons: components["schemas"]["RejectReasonRef"][] | undefined }) {
  if (!reasons?.length) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="mr-0.5 text-caption text-label-3">반려 사유</span>
      {reasons.map((r) => (
        <Chip key={r.code} tone="bad">{r.label}</Chip>
      ))}
    </span>
  );
}
