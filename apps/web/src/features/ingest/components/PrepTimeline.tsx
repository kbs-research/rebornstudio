/**
 * 영상 준비 타임라인.
 *
 * 단계마다 지금 상태를 한 줄로 두고, 실패는 그 줄에서 사유를 펼치고 재시도·건너뛰기를 부른다.
 *
 * 규칙:
 *  - 시각·소요·시도 수는 쓰지 않는다. 지금 상태와 막힌 이유만 보여 준다.
 *  - 건너뛴 단계도 이유와 함께 보여 준다. 숨기면 이 영상만 단계가 다른 이유를 알 수 없다.
 *  - 사람이 실행해야 넘어가는 단계(`needed`)는 호박색으로 두고 실행 버튼을 그 줄의 주 동작으로 둔다.
 *    건너뜀과 같은 회색이면 진행을 막는 단계가 무해한 단계처럼 보인다.
 *  - 서버가 확인 필요(`review_needed`)로 표시한 비식별화는 완료여도 경고 줄을 둔다.
 *    가릴 얼굴을 찾지 못한 결과는 원본과 같아서 완료만 보이면 얼굴이 그대로 나가는지 알 수 없다.
 */
import type { ReactNode } from "react";
import { useState } from "react";
import { ChevronRight, Play, RotateCw, SkipForward, TriangleAlert } from "lucide-react";

import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/Button";
import {
  RUN_STAGE_LABEL, RUN_STATUS_LABEL, RUN_STATUS_TONE, TONE_TEXT, type PrepTimelineEntry,
} from "../lib/vocab";
import { failedRowAction, retryOffered } from "../lib/row_action";
import { StatusMark } from "./StatusMark";

/** 영상 준비 타임라인을 그린다. 단계마다 상태와 사유를 한 줄씩 보여 준다. */
export function PrepTimeline({
  entries, onRetry, onSkip, retryBusy, action, openFailed = false, retryLabels,
}: {
  entries: PrepTimelineEntry[];
  /** 실패한 단계를 다시 실행한다. 지원하는 단계에서만 버튼이 뜬다. */
  onRetry?: (stage: PrepTimelineEntry["stage"]) => void;
  /**
   * 단계별 재시도 버튼 이름을 바꾼다. 장면 요약을 받지 못한 영상은 요약 줄의 버튼이 `요약 재요청`이다.
   * 어느 영상이 그 경우인지는 서버가 정한다(`prep_blocked_stage`).
   */
  retryLabels?: Partial<Record<PrepTimelineEntry["stage"], string>>;
  /** 실패한 단계를 건너뛴다. 서버가 `skippable`로 연 줄에서만 버튼이 뜬다. */
  onSkip?: (stage: PrepTimelineEntry["stage"]) => void;
  retryBusy?: boolean;
  action?: ReactNode;
  /**
   * 실패한 단계의 사유를 펼친 채로 열고 그 줄을 옅은 위험색 면으로 두면 true.
   * 실행이 필요한 단계(`needed`)는 옅은 호박색 면으로 둔다.
   * 막힌 영상의 상세가 켠다. 멈춘 단계를 확인하려고 여는 화면이라 사유를 펼쳐 둔다.
   */
  openFailed?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-card border border-separator bg-panel shadow-e1">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-separator px-4 py-2.5">
        {/* 본문 카드 제목은 클립 카드와 같은 모양과 층위(h2)로 둔다. */}
        <h2 className="text-callout font-semibold text-label">준비 진행</h2>
        {/*
          단계 이름을 화살표로 이은 요약 줄은 두지 않는다. 바로 아래 목록이 같은 단계를 같은 순서로 보여 준다.
        */}
        {action && <div className="ml-auto flex items-center gap-2">{action}</div>}
      </header>
      <ol className="px-4 py-1">
        {entries.map((e, i) => (
          <TimelineRow
            key={e.stage}
            entry={e}
            last={i === entries.length - 1}
            onRetry={onRetry}
            onSkip={onSkip}
            retryBusy={retryBusy}
            openFailed={openFailed}
            retryLabel={retryLabels?.[e.stage]}
          />
        ))}
      </ol>
    </section>
  );
}

/** 타임라인의 단계 한 줄을 그린다. 실패 사유는 펼쳐서 본다. */
function TimelineRow({
  entry, last, onRetry, onSkip, retryBusy, openFailed = false, retryLabel,
}: {
  entry: PrepTimelineEntry;
  last: boolean;
  onRetry?: (stage: PrepTimelineEntry["stage"]) => void;
  onSkip?: (stage: PrepTimelineEntry["stage"]) => void;
  retryBusy?: boolean;
  openFailed?: boolean;
  /** 재시도 버튼 이름. 없으면 단계 이름표(`RETRY_LABEL`)를 쓴다. */
  retryLabel?: string;
}) {
  const highlight = openFailed && (entry.status === "failed" || entry.status === "needed");
  const [open, setOpen] = useState(highlight && entry.status === "failed");
  const tone = RUN_STATUS_TONE[entry.status];
  const muted = entry.status === "pending" || entry.status === "skipped";
  /* 실패 줄의 조치는 재시도와 건너뛰기 중 하나다(`failedRowAction`). */
  const failAction = failedRowAction(entry, {
    retry: retryOffered(entry, !!onRetry && RETRYABLE.has(entry.stage)), skip: !!onSkip,
  });
  const canRetry = failAction === "retry";
  const canSkip = failAction === "skip";

  return (
    <li
      className={cn(
        "relative flex gap-3 py-2",
        highlight && "-mx-2 rounded-control px-2",
        highlight && (entry.status === "needed" ? "bg-amber-soft" : "bg-bad-muted"),
      )}
    >
      {/* 레일 선. 마지막 행은 선을 잇지 않아 끝이 어디인지 보이게 한다. */}
      {!last && (
        <span
          /* 면을 칠한 줄은 안쪽 여백만큼 표시가 오른쪽으로 가므로 선도 같이 옮긴다. */
          className={cn("absolute top-7 bottom-0 w-px bg-separator", highlight ? "left-[15px]" : "left-[7px]")}
          aria-hidden="true"
        />
      )}
      <StatusMark status={entry.status} size={16} className="mt-0.5" />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className={cn("text-footnote font-medium", muted ? "text-label-3" : "text-label")}>
            {RUN_STAGE_LABEL[entry.stage]}
          </span>
          <span className={cn("text-caption", TONE_TEXT[tone])}>
            {RUN_STATUS_LABEL[entry.status]}
          </span>
          {/*
            진행률. 변환은 한 시간을 넘길 수 있어 진행 중이라는 상태만으로는 남은 양을 알 수 없다.
            서버가 값을 주지 못하면(분자나 분모가 없음) 아무것도 그리지 않는다.
          */}
          {entry.status === "running" && entry.progress != null && (
            <span className="ml-auto inline-flex items-center gap-1.5 text-caption">
              <span className="h-1 w-14 overflow-hidden rounded-capsule bg-fill" aria-hidden="true">
                {/*
                  전환 시간은 500ms로, 업로드 막대(200ms)보다 길게 둔다.
                  이 값은 10~15초 폴링으로 갱신되어 한 번에 크게 바뀌므로 전환이 짧으면 막대가 건너뛰어 보인다.
                */}
                <span
                  className="block h-full rounded-capsule bg-accent transition-[width] duration-500"
                  style={{ width: `${Math.max(entry.progress * 100, 2)}%` }}
                />
              </span>
              <span className={cn("tabular-nums", TONE_TEXT.run)}>
                {Math.round(entry.progress * 100)}%
              </span>
            </span>
          )}
        </div>

        {/* 보조 줄. 건너뜀 이유, 자동 재시도·처리 서버 무응답·앞 단계 대기 안내, 실패 사유(펼침)를 보여 준다. */}
        {entry.skipped_reason && (
          <p className="mt-0.5 text-caption text-label-3">{entry.skipped_reason}</p>
        )}
        {entry.review_needed && (
          <p className="mt-0.5 flex items-center gap-1 text-caption text-warn-text">
            <TriangleAlert size={12} className="shrink-0" aria-hidden="true" />
            {entry.review_note ?? "비식별화 결과를 확인해야 합니다."}
          </p>
        )}
        {entry.note && <p className="mt-0.5 text-caption text-label-3">{entry.note}</p>}

        {/* 실행해야 넘어가는 단계. 실패가 아니라서 사유 펼침이 없고, 사유는 위의 `note` 줄이 보여 준다.
            실행 가능 여부는 서버의 `status`·`retryable`을 쓴다. 화면은 `deident_enabled`를 받지 않는다.
            진행을 막는 단계를 푸는 동작이라 주 버튼으로 두고, 이름은 설정의 기능 이름(`비식별화 실행`)을 쓴다. */}
        {entry.status === "needed" && entry.retryable && onRetry && (
          <div className="mt-1.5">
            <Button size="sm" disabled={retryBusy} onClick={() => onRetry(entry.stage)}>
              <Play size={14} aria-hidden="true" /> 비식별화 실행
            </Button>
          </div>
        )}
        {/*
          실패 줄. 사유가 있을 때만 `사유 보기`를 둔다. 사유가 없으면 서버가 비워 준다.
          상태 이름(`실패`)을 펼침 안에서 되풀이하지 않는다.
          재시도는 사유가 없어도 되살릴 수 있으므로 사유와 관계없이 둔다.
        */}
        {entry.status === "failed" && (entry.error || canRetry || canSkip) && (
          <div className="mt-1">
            <div className="flex flex-wrap items-center gap-2">
              {entry.error && (
                <button
                  type="button"
                  onClick={() => setOpen((o) => !o)}
                  aria-expanded={open}
                  className="inline-flex items-center gap-1 text-caption text-label-3 transition-colors hover:text-label"
                >
                  <ChevronRight
                    size={12}
                    className={cn("shrink-0 transition-transform", open && "rotate-90")}
                    aria-hidden="true"
                  />
                  사유 보기
                </button>
              )}
              {/* 재시도 버튼은 사유 바로 옆에 둔다.
                  비가역이거나 비용이 다시 드는 동작의 판단 근거는 같은 화면에 둔다. */}
              {canRetry && (
                <button
                  type="button"
                  disabled={retryBusy}
                  onClick={() => onRetry?.(entry.stage)}
                  className="inline-flex items-center gap-1 rounded-control border border-separator px-2 py-0.5 text-caption font-medium text-label-2 transition-colors hover:bg-fill disabled:opacity-50"
                >
                  <RotateCw size={12} aria-hidden="true" /> {retryLabel ?? RETRY_LABEL[entry.stage] ?? "다시 시도"}
                </button>
              )}
              {/* 확인 창을 거친다. 판단 근거(사유)는 같은 줄의 `사유 보기`와 확인 창에 있다. */}
              {canSkip && (
                <button
                  type="button"
                  disabled={retryBusy}
                  onClick={() => onSkip?.(entry.stage)}
                  className="inline-flex items-center gap-1 rounded-control border border-separator px-2 py-0.5 text-caption font-medium text-label-2 transition-colors hover:bg-fill disabled:opacity-50"
                >
                  <SkipForward size={12} aria-hidden="true" /> 비식별화 건너뛰기
                </button>
              )}
            </div>
            {open && entry.error && (
              <p className="mt-1 whitespace-pre-wrap break-words rounded-control bg-bad-muted px-2.5 py-1.5 text-caption text-bad-text">
                {entry.error}
              </p>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

/**
 * 재시도 버튼 이름. 단계마다 부르는 요청이 달라 이름도 그 동작으로 쓴다.
 * 토스트·확인 창과 같은 이름이다(`변환 재시도 실패`, `분석 재요청` 창, `비식별화 재시도 실패`).
 * 분석 재요청은 비용이 다시 드는 요청이라 이름으로 구분한다.
 * 규격 재검사(`probe/reset`)는 카드 머리 버튼 없이 이 줄 하나가 부른다.
 */
const RETRY_LABEL: Partial<Record<PrepTimelineEntry["stage"], string>> = {
  probe: "규격 재검사",
  transcode: "변환 재시도",
  publish: "변환 재시도",
  register: "분석 재요청",
  analyze: "분석 재요청",
  summarize: "분석 재요청",
  deident: "비식별화 재시도",
  scene_split: "장면 분할 재시도",
};

/**
 * 화면에서 재시도를 제안할 단계. 복구 경로가 있는 단계만 넣는다.
 * probe는 규격 재검사(`probe/reset`), transcode와 publish는 변환 재시도(`transcode/retry`)로 복구한다.
 * transcode 실패는 규격을 이미 통과한 영상이라 규격 재검사를 부르면 항상 409 응답을 받는다.
 */
const RETRYABLE = new Set<PrepTimelineEntry["stage"]>([
  // 분석 등록에서 멈춘 영상도 분석 재요청이 등록부터 다시 한다(서버 `maiu.retry_failed`의 restart).
  // 서버는 자산 번호가 없을 때 등록 실패를 이 줄에 싣는다.
  "register",
  "probe", "transcode",
  // 산출물 검사가 재생본도 불합격 처리할 수 있어서 재생본 변환도 `변환 재시도`로 복구한다.
  // 서버가 분석본과 재생본 중 멈춘 쪽을 다시 실행한다(`transcode.retry`).
  "publish",
  // 분석과 요약 실패도 되살린다. 자산은 그대로 두고 분석만 다시 요청한다.
  // 장면 요약을 받지 못한 영상은 서버가 요약만 다시 요청한다.
  // 요약 줄은 서버가 재요청을 받을 수 있다고 연 줄(`retryable`)에만 버튼을 둔다(`retryOffered`).
  "analyze", "summarize",
  // 비식별화도 같은 이유로 넣는다. 없으면 실패한 영상에 재시도 버튼이 나오지 않는다.
  // 완료된 비식별화의 재실행은 서버가 거부한다. 다시 실행하면 이미 가린 영상을 또 가리게 된다.
  "deident",
  // 자체 장면 분할의 실패도 재시도할 수 있게 한다. 클립이 이미 있으면 중복 클립을 막으려고 서버가 거부한다.
  "scene_split",
]);
