/**
 * 두 대기열(라벨링·검수)이 함께 쓰는 다음 작업 카드.
 *
 * 한 사람은 역할마다 한 건씩 맡는다. 화면의 할 일은 다음 작업 버튼 하나다.
 * 서버가 보유한 작업이 있으면 그 작업을 다시 주고, 없으면 대기열에서 한 건을 배정한다(`work/next`·`review/next`).
 * 그래서 이 카드는 고를 수 있는 목록을 그리지 않는다. 보유한 작업이 있으면 그 작업을 미리 보여 준다.
 *
 * 이 카드는 다음을 보장한다.
 * - 대기 수, 빈 상태, 버튼의 낱말은 `QUEUE_SPEC` 한 곳에서 나온다.
 * - 대기 수에는 단위를 붙이고 줄이지 않는다. `99+`는 좁은 배지에만 쓴다.
 * - 조회 실패를 빈 상태로 그리지 않는다. 대기 수 실패는 칩이, 다음 작업 실패는 배너가 알린다.
 */
import type { ReactNode } from "react";
import { ArrowRight, ClipboardCheck, ListTodo, TriangleAlert, type LucideIcon } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { LastUpdated } from "@/shared/ui/LastUpdated";
import { Skeleton } from "@/shared/ui/Skeleton";
import { errorText } from "@/shared/api/error_text";

import { HeaderActions } from "./HeaderActions";

/** 큐 종류. 두 대기열을 구분하는 유일한 값이다. */
export type QueueKind = "label" | "review";

/**
 * 큐마다 다른 낱말과 아이콘. 카드·빈 상태·버튼이 모두 이 표를 읽는다.
 *
 * `unit`은 그 큐가 세는 대상의 이름이고 `counter`는 단위다. 작업은 `건`이다.
 * `nextLabel`은 주 버튼의 이름이다. 라벨링은 `다음 작업`, 검수는 `다음 검수`다.
 * `poolWord`는 대기 수의 수식어다. 라벨링의 대기 수는 누구나 받을 수 있는 수라서 `대기`라고 쓴다.
 * 검수는 서버가 호출한 사용자가 라벨링하지 않은 작업만 센다(`_review_claimable_where`).
 * 그래서 단계 배지가 `검수 14`여도 카드에 `받을 수 있는 검수 작업 0건`이 뜰 수 있고, 두 수는 모두 맞다.
 * `poolNote`는 대기 수에서 빠진 작업의 설명이다. 없으면 작업이 있는데 없다고 표시하는 것처럼 보인다.
 */
export const QUEUE_SPEC: Record<
  QueueKind,
  {
    unit: string; counter: "건" | "개"; title: string; cardTitle: string; Icon: LucideIcon;
    poolWord: string; poolNote?: string; nextLabel: string;
  }
> = {
  label: {
    unit: "작업", counter: "건", title: "라벨링", cardTitle: "내 라벨링 작업", Icon: ListTodo,
    poolWord: "대기", nextLabel: "다음 작업",
  },
  review: {
    unit: "검수 작업", counter: "건", title: "검수", cardTitle: "내 검수 작업", Icon: ClipboardCheck,
    poolWord: "받을 수 있는",
    poolNote: "직접 라벨링한 작업과 다른 검수자가 맡은 작업은 제외됩니다.",
    nextLabel: "다음 검수",
  },
};

/** 대기 수를 단위와 붙여 쓴다. 예: `대기 작업 147건`. */
function poolPhrase(kind: QueueKind, n: number): string {
  const spec = QUEUE_SPEC[kind];
  return `${spec.poolWord} ${spec.unit} ${n.toLocaleString("ko-KR")}${spec.counter}`;
}

/**
 * 카드 머리의 대기 수 문구를 만든다. 수는 줄이지 않고 배지와 같은 `toLocaleString`으로 쓴다.
 * 값이 없으면 빈 목록을 돌려준다. 조회 전이나 실패를 `0`으로 쓰면 할 일이 없다는 뜻으로 읽힌다.
 */
export function queueCountLabels(kind: QueueKind, pool?: number | null): string[] {
  return pool != null ? [poolPhrase(kind, pool)] : [];
}

/** 대기 수 조회에 실패해 칩으로 알려야 하면 true. 대기열 화면들이 같은 판정을 쓴다. */
export function poolFailed(q: { isError: boolean; error: unknown }): boolean {
  return q.isError;
}

/** 다음 작업 실패 배너에 넘기는 문구. 굵은 결과와 작은 안내다. */
export interface NextErrorText {
  message: string;
  hint?: string;
}

/** 다음 작업 실패 문구를 만든다. 사전(`errorText`)이 대기 없음·일시정지·권한 없음을 각각 안내한다. */
export function nextErrorText(detail: unknown): NextErrorText {
  return errorText(detail);
}

interface Props {
  kind: QueueKind;
  /**
   * 단계 화면이나 탭 안에 넣으면 true. 카드 제목을 `내 라벨링 작업`처럼 쓴다.
   * 그 위치에서는 레일이나 탭 이름이 이미 단계 이름을 보여 주므로 같은 낱말을 되풀이하지 않는다.
   */
  embedded?: boolean;
  /** 서버가 센 대기 수. `null`/`undefined`는 아직 값이 없다는 뜻이라 칩을 그리지 않는다. */
  pool?: number | null;
  /** 대기 수 조회에 실패했으면 true. 실패를 `0`으로 읽으면 할 일이 없다는 뜻이 되므로 따로 받는다. */
  poolError?: boolean;
  /** 서버가 센 보유 수. `null`은 아직 값이 없다는 뜻이다. 재작업이 돌아오면 2일 수 있다. */
  held: number | null;
  /**
   * 다음 작업이 다시 열 보유 작업의 미리보기. 보유한 작업이 없으면 `null`이다.
   * 그리는 모양은 호출부가 정한다. 작업 단계 화면은 클립 칩을, 내 작업은 캡션 미리보기와 반려 사유를 쓴다.
   */
  current: ReactNode | null;
  /** 보유 목록을 처음 받는 중이면 true. */
  loading?: boolean;
  /** 보유 목록 조회 실패 객체. 값이 있으면 빈 상태 대신 실패를 그린다. */
  listError?: unknown;
  updatedAt?: number;
  /**
   * 새로고침. 보유 목록과 대기 수를 함께 다시 받는다.
   * 대기 수가 0이면 다음 작업이 잠긴다. 폴링 사이에 큐가 채워지면 이 버튼이 잠금을 푼다.
   */
  onRefresh: () => void;
  refreshing?: boolean;
  onNext: () => void;
  nexting?: boolean;
  nextError?: NextErrorText | null;
}

/**
 * 다음 작업 카드를 그린다. 머리(제목·대기 수·보유 수), 본문(보유 작업 미리보기나 빈 상태), 주 버튼 순서다.
 *
 * 보유한 작업이 없고 대기 수가 0이면 버튼을 잠근다. 대기 수를 모르면 잠그지 않는다.
 * 대기 수 조회의 일시 오류로 작업이 멈추지 않게 한다. 받을 작업이 없으면 서버가 404를 돌려주고 배너가 사유를 표시한다.
 */
export function NextWorkCard({
  kind,
  embedded = false,
  pool,
  poolError = false,
  held,
  current,
  loading = false,
  listError,
  updatedAt,
  onRefresh,
  refreshing,
  onNext,
  nexting = false,
  nextError,
}: Props) {
  const spec = QUEUE_SPEC[kind];
  const holding = held != null && held > 0;
  const poolKnown = pool != null;
  const nothing = !holding && current == null && pool === 0;
  const disabled = nexting || nothing || (held == null && loading);

  const nextButton = (
    <Button onClick={onNext} disabled={disabled}>
      {nexting ? "여는 중…" : spec.nextLabel}
      <ArrowRight size={14} aria-hidden="true" />
    </Button>
  );

  let body: ReactNode;
  if (loading && current == null) {
    body = <Skeleton className="h-10 w-full" />;
  } else if (listError && current == null) {
    body = <ErrorState inline message="보유 작업 조회 실패" error={listError} onRetry={onRefresh} />;
  } else if (current != null) {
    body = (
      <div className="space-y-1.5">
        <p className="text-caption font-semibold text-label-2">보유 중인 작업</p>
        <div className="min-w-0">{current}</div>
        <p className="text-footnote text-label-3">보유한 작업부터 이어서 엽니다.</p>
      </div>
    );
  } else if (nothing) {
    body = <EmptyState dense message={`${spec.poolWord} ${spec.unit} 없음`} />;
  } else {
    body = <p className="text-footnote text-label-2">대기열에서 한 건을 받아 바로 엽니다.</p>;
  }

  return (
    <div className="space-y-4">
      {/* 갱신 시각은 작업 단계 화면 머리 오른쪽에 둔다. 머리가 없는 화면에서는 이 위치에 그린다. */}
      <HeaderActions>
        <LastUpdated at={updatedAt} onRefresh={onRefresh} refreshing={refreshing} />
      </HeaderActions>
      {/* 다음 작업 실패는 이유와 할 일을 두 줄로 보여 준다. 받지 못한 상태가 이어지므로 토스트 대신 배너를 쓴다. */}
      {nextError && (
        <div role="alert" className="rounded-card border border-warn/30 bg-warn-muted px-4 py-3 text-warn-text">
          <p className="flex items-center gap-1.5 text-footnote font-semibold">
            <TriangleAlert size={14} aria-hidden="true" /> {nextError.message}
          </p>
          {nextError.hint && <p className="mt-1 text-footnote text-label">{nextError.hint}</p>}
        </div>
      )}
      <Card as="section" aria-label={embedded ? spec.cardTitle : spec.title}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-separator px-4 py-3">
          <h3 className="text-headline text-label">{embedded ? spec.cardTitle : spec.title}</h3>
          <p className="flex flex-wrap items-baseline gap-x-2 text-footnote tabular-nums text-label-2">
            {queueCountLabels(kind, pool).map((label) => (
              <span key={label}>{label}</span>
            ))}
            {holding && (
              <span className="inline-flex items-baseline gap-2">
                {poolKnown && <span aria-hidden="true" className="text-label-4">·</span>}
                보유 중 <b className="font-semibold text-label">{held.toLocaleString("ko-KR")}</b>{spec.counter}
              </span>
            )}
          </p>
          {/* 대기 수가 없으면 0처럼 보이지 않게 경고 칩을 단다. 다음 작업은 막지 않는다. */}
          {poolError && <Chip tone="amber">대기 수 모름</Chip>}
        </div>
        {/* 대기 수에서 빠진 작업을 설명하는 줄. 대기 수가 없을 때 붙이면 조회 실패 위에 설명이 표시되어 사실과 달라진다. */}
        {spec.poolNote && poolKnown && (
          <p className="border-b border-separator px-4 py-2 text-caption text-label-3">{spec.poolNote}</p>
        )}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4">
          <div className="min-w-0 flex-1">{body}</div>
          <div className="shrink-0">{nextButton}</div>
        </div>
      </Card>
    </div>
  );
}
