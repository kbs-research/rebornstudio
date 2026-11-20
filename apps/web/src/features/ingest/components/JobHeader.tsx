/**
 * 등록 Job 상세의 머리.
 *
 * 이 Job에서 확인할 정보를 한 화면에서 모두 보여 준다.
 *   - 정체: 이름(바로 수정 가능), 출처, 수행자, 등록 시각
 *   - 규모: 영상 수(삭제분 포함), 총 길이, 클립 수. 클립 수는 분석을 마친 영상이 생긴 뒤에만 보인다.
 *   - 진행: 전체 폭의 준비 분포 막대
 *
 * 세로로 쌓지 않고 세 줄 안에 넣는다. 머리가 길면 목록이 아래로 밀린다.
 */
import { type ReactNode, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FolderInput, Loader2, Pencil, UploadCloud, X } from "lucide-react";

import { Chip } from "@/shared/ui/Chip";
import { type ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { errorText } from "@/shared/api/error_text";
import { relativeTime } from "@/shared/lib/time";
import { useToast } from "@/shared/ui/Toast";
import { type Segment } from "./SegmentBar";
import { GROUP_LABEL, SOURCE_LABEL, SOURCE_REGISTER_LABEL, TONE_FILL, fmtElapsed } from "../lib/vocab";
import { ingestKeys, renameIngestBatch, type IngestJobDetail as JobDetail } from "../api";

/** Job 이름을 돌려준다. 이름이 없으면 등록 시각과 출처로 만든다. 그래서 이름 입력은 선택 사항이다. */
export function jobTitle(j: { label?: string | null; created_at: string; source: string }): string {
  if (j.label) return j.label;
  const d = new Date(j.created_at);
  const when = `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${when} ${SOURCE_REGISTER_LABEL[j.source] ?? "등록"}`;
}

/** 등록 Job 상세의 머리를 그린다. 정체, 규모, 진행을 한곳에 보여 준다. */
export function JobHeader({
  job, onPick, activeChip, actions,
}: {
  job: JobDetail;
  onPick: (key: string) => void;
  activeChip: string | null;
  /** 이름 줄 오른쪽에 둘 묶음 단위 동작(메타 일괄 입력 등). 권한 판정은 호출하는 쪽이 한다. */
  actions?: ReactNode;
}) {
  const pc = job.prep;
  const importing = job.import_status === "pending" || job.import_status === "running";

  /*
    진행 분포는 끝난 일을 먼저 보여 주도록 완료부터 채운다.
    그 뒤는 완료에 가까운 단계부터 준비 순서를 거슬러 두어 진행 방향으로 읽히게 한다. 이슈는 항상 마지막이다.
    문구는 칩, 레일과 같게 쓴다.
  */
  const segments: Segment[] = [
    { key: "done", label: "완료", n: pc.ready_for_work, fill: TONE_FILL.ok },
    { key: "analyze", label: GROUP_LABEL.analyze, n: pc.analyzing + pc.summarizing, fill: TONE_FILL.run },
    { key: "mi", label: GROUP_LABEL.mi, n: pc.registering, fill: TONE_FILL.run },
    { key: "awaiting", label: "요청 대기", n: pc.awaiting_analysis, fill: TONE_FILL.wait },
    { key: "transcode", label: "변환", n: pc.transcoding, fill: "bg-accent/70" },
    { key: "register", label: "등록", n: pc.uploading + pc.probing, fill: "bg-accent/70" },
    { key: "blocked", label: "문제", n: pc.blocked, fill: TONE_FILL.bad },
  ];

  /* 예상 금액은 머리에 두지 않는다. 이 금액은 길이와 전역 시간당 단가로 계산한 추정치라 머리에 두면 확정 청구액처럼 읽힌다.
     단가는 프로젝트 스택과 관계없는 전역 값이라 벤더를 호출하지 않는 스택(`ncp-shots`)에서도 표시된다.
     금액은 결정하는 곳(분석 요청 확인 창)에 두고, 무엇을 합한 금액인지 밝힌다. */

  return (
    <header className="space-y-4 px-5 pb-4 pt-1">
      {/* 이름을 제목 크기로 두고, 그 아래 출처·시각·등록한 사람을 한 줄에 둔다. */}
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <NameCell job={job} />
          {importing && (
            <Chip tone="accent">
              <Loader2 size={12} className="animate-spin" aria-hidden="true" /> 등록 중
            </Chip>
          )}
          {actions && <span className="ml-auto flex items-center gap-2">{actions}</span>}
        </div>
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-footnote text-label-3">
          {job.source === "ncp"
            ? <FolderInput size={12} aria-hidden="true" />
            : <UploadCloud size={12} aria-hidden="true" />}
          <span>{SOURCE_REGISTER_LABEL[job.source] ?? SOURCE_LABEL[job.source] ?? job.source}</span>
          <span aria-hidden="true">·</span>
          <span>{relativeTime(job.created_at)}</span>
          {job.created_by_name && <><span aria-hidden="true">·</span><span>{job.created_by_name}</span></>}
          {job.import_prefix && (
            <span className="min-w-0 truncate font-mono" title={job.import_prefix}>· {job.import_prefix}</span>
          )}
          {/* 같은 폴더를 다시 등록하면 이미 등록된 파일은 건너뛴다. 건너뛴 수를 밝히지 않으면 폴더보다 영상이 적어 빠진 것처럼 보인다. */}
          {job.import_skipped_existing > 0 && (
            <span title="이미 등록된 파일이라 이 묶음에 다시 등록하지 않은 수입니다.">
              · {job.import_skipped_existing.toLocaleString("ko-KR")}건 건너뜀
            </span>
          )}
          {/* 자유 메모라 길이 제한이 없다. 원문은 `title`로 남긴다. */}
          {job.note && <span className="min-w-0 truncate" title={job.note}>· {job.note}</span>}
        </p>
      </div>

      {/* 수치는 라벨 위에 값을 둔다. 수마다 라벨이 있어야 무엇의 길이인지 읽힌다. */}
      <dl className="flex flex-wrap gap-x-10 gap-y-3">
        <BigStat label="영상">
          {job.video_count.toLocaleString("ko-KR")}<Unit>개</Unit>
          {/* 삭제분을 밝힌다. 밝히지 않으면 영상을 모두 지운 묶음이 '0개 완료'로 보인다. */}
          {job.deleted_videos > 0 && (
            <span className="ml-1.5 text-caption font-normal text-label-3">삭제 {job.deleted_videos.toLocaleString("ko-KR")}개</span>
          )}
        </BigStat>
        {job.total_duration_seconds > 0 && (
          <BigStat label="총 길이">{fmtElapsed(job.total_duration_seconds)}</BigStat>
        )}
        {/* 분석을 마친 영상이 생긴 뒤에만 그린다. 그 뒤의 0은 그대로 그린다. */}
        {(job.clip_count > 0 || pc.ready_for_work + pc.blocked > 0) && (
          <BigStat label="클립">{job.clip_count.toLocaleString("ko-KR")}<Unit>개</Unit></BigStat>
        )}
      </dl>

      {/*
        준비 분포 막대는 전체 폭으로 그린다. 바로 아래 상태 필터가 같은 순서라 막대와 필터가 한 축으로 읽힌다.
        조각을 누르면 그 상태로 거른다. 막대는 보조 수단이고 주 조작은 필터다.
      */}
      {pc.total > 0 && (
        <span className="flex h-1.5 w-full gap-0.5 overflow-hidden rounded-capsule bg-canvas-2">
          {segments.filter((x) => x.n > 0).map((x) => (
            <button
              key={x.key}
              type="button"
              tabIndex={-1}
              aria-hidden="true"
              onClick={() => onPick(x.key)}
              className={cn("h-full min-w-2", x.fill)}
              style={{ flexGrow: x.n }}
            />
          ))}
        </span>
      )}

      {/*
        등록 작업 실패를 알린다. 영상 수가 늘지 않은 이유라서 숫자보다 먼저 보여 준다.
        재시도 횟수를 문구에 고정하지 않는다. 사유는 서버가 관리자용 문장으로 남긴다.
      */}
      {job.import_status === "failed" && (
        <p className="flex flex-wrap items-baseline gap-x-2 text-footnote">
          <b className="font-semibold text-bad-text">등록 작업 실패</b>
          <span className="text-label-3">
            {job.import_error ?? "재시도 후 중단"}
            {job.video_count > 0 ? ` · 영상 ${job.video_count.toLocaleString("ko-KR")}개 등록` : ""}
          </span>
        </p>
      )}
      <span className="sr-only">{activeChip ? `필터: ${activeChip}` : "전체 보기"}</span>
    </header>
  );
}

/** 라벨 위에 값을 두는 수치 하나. */
function BigStat({
  label, children,
}: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-caption text-label-3">{label}</dt>
      <dd className="text-headline font-bold tabular-nums text-label">{children}</dd>
    </div>
  );
}

/** 큰 수 옆의 단위. */
function Unit({ children }: { children: ReactNode }) {
  return <span className="ml-1 text-caption font-normal text-label-3">{children}</span>;
}


/** 이름 셀. 이 위치에서 바로 이름을 고친다. 이름 수정을 별도 화면에 두면 사용자가 이름을 거의 달지 않는다. */
function NameCell({ job }: { job: JobDetail }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(job.label ?? "");

  const save = useMutation({
    mutationFn: () => renameIngestBatch(job.batch_id, draft.trim()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ingestKeys.jobAll() });
      void qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
      setEditing(false);
      toast("success", "묶음 이름을 저장했습니다.");
    },
    // 원인이 확인되지 않으면 "권한을 확인해주세요"로 단정하지 않는다. 오류 사전에 있는 원인만 알린다.
    onError: (e) => {
      const t = errorText((e as unknown as ApiError | null)?.detail, { message: "묶음 이름 저장 실패" });
      toast("error", t.message, t.hint);
    },
  });

  if (editing) {
    return (
      <span className="flex items-center gap-1.5">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save.mutate();
            if (e.key === "Escape") setEditing(false);
          }}
          placeholder={jobTitle(job)}
          aria-label="묶음 이름"
          className="w-64 rounded-field border border-separator bg-canvas px-2 py-1 text-callout text-label outline-none focus:border-focus"
        />
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}
          aria-label="저장" className="rounded-control p-1 text-ok-text hover:bg-fill">
          <Check size={14} />
        </button>
        <button type="button" onClick={() => setEditing(false)}
          aria-label="취소" className="rounded-control p-1 text-label-4 hover:bg-fill">
          <X size={14} />
        </button>
      </span>
    );
  }
  return (
    <span className="group/name flex min-w-0 items-center gap-1.5">
      <h3 className={cn("truncate text-title-1", job.label ? "text-label" : "text-label-2")}>
        {jobTitle(job)}
      </h3>
      {/* 누르는 영역은 24px 이상이다(WCAG 2.5.8). 키보드 포커스를 받으면 보이게 한다. */}
      <button
        type="button"
        onClick={() => { setDraft(job.label ?? ""); setEditing(true); }}
        aria-label="묶음 이름 수정"
        className="inline-flex size-7 items-center justify-center rounded-control text-label-3 opacity-0 transition-opacity hover:bg-fill hover:text-label-2 focus-visible:opacity-100 group-hover/name:opacity-100"
      >
        <Pencil size={14} aria-hidden="true" />
      </button>
    </span>
  );
}
