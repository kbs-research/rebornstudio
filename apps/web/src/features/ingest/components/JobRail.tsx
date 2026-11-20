/**
 * Job 상세 오른쪽 레일. 이 Job에서 지금 할 수 있는 조치 카드만 둔다. 지금 카드는 분석 요청 하나다.
 *
 * 해당하지 않는 카드는 그리지 않는다. 단계 분포는 목록 위 단계 칩 줄이, 문제 영상의 위치별 수는
 * 목록 위 문제 배너가 보여 주고, 문제 조치는 행마다 `문제 해결` 메뉴가 맡는다.
 * 수는 카드 머리 아래에 크게 한 번만 둔다.
 *
 * 일괄 대상은 화면에 받은 50건이 아니라 조건에 맞는 전체다.
 * 전체 수는 `/videos/select`가 센다. 상한에 걸리면 호출하는 쪽이 그 사실을 밝힌다.
 */
import type { ReactNode } from "react";

import { Button } from "@/shared/ui/Button";
import type { PrepStageCounts } from "../lib/vocab";

/**
 * 레일에 그릴 카드가 하나라도 있으면 true를 돌려준다.
 *
 * 없으면 호출하는 쪽이 레일 영역을 비운다. 카드를 그리는 조건과 같은 식을 쓴다.
 */
export function jobRailHasCards({
  counts, canAct,
}: {
  counts: PrepStageCounts;
  canAct?: boolean;
}): boolean {
  return !!canAct && counts.awaiting_analysis > 0;
}

/** Job 상세 오른쪽 레일을 그린다. 분석 요청 카드를 둔다. */
export function JobRail({
  counts, busy, canAct, onDispatch, costFree,
}: {
  counts: PrepStageCounts;
  /** 레일 동작이 실행 중이면 true. 겹쳐 누르면 같은 대상에 요청이 두 번 나간다. */
  busy?: boolean;
  canAct?: boolean;
  onDispatch: () => void;
  /**
   * 이 스택에 분석 비용이 없으면 true(벤더 분석기 없음, `useAnalysisStack`).
   * 스택 정보가 없으면 `false`로 둔다. 비용이 드는 결정 앞에서는 비용이 있다고 알리는 쪽이 안전하다.
   */
  costFree?: boolean;
}) {
  const waiting = counts.awaiting_analysis;

  return (
    <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-75">
      {/* 분석 요청 카드. 비용이 드는 유일한 동작이라 채운 버튼을 여기에만 쓴다. */}
      {canAct && waiting > 0 && (
        <Block title="분석 요청" n={waiting} unit="요청 대기">
          {/*
            버튼에 금액을 넣지 않는다. 금액은 화면이 길이와 단가로 추정한 값이라
            버튼에 붙으면 확정 청구액으로 읽힌다. 금액은 확인 창이 판단 근거로 보여 준다.
            벤더 분석기가 없는 스택은 비용이 없다고 알린다.
          */}
          <p className="text-caption text-label-3">
            {costFree ? "분석 비용이 들지 않습니다." : "금액은 요청할 때 확정됩니다."}
          </p>
          <Button className="w-full" disabled={busy} onClick={onDispatch}>
            영상 {waiting.toLocaleString("ko-KR")}개 분석 요청
          </Button>
        </Block>
      )}
    </aside>
  );
}

/** 레일 카드 하나. 머리 라벨 아래에 결정의 크기(수와 단위)를 크게 둔다. */
function Block({
  title, n, unit, children,
}: {
  title: string;
  n: number;
  unit?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-card border border-separator bg-panel p-4 shadow-e1">
      <div className="space-y-1">
        <h4 className="text-caption font-semibold text-label-3">{title}</h4>
        <p className="flex items-baseline gap-1">
          <span className="text-title-1 tabular-nums text-label">{n.toLocaleString("ko-KR")}</span>
          <span className="text-caption text-label-3">개{unit ? ` ${unit}` : ""}</span>
        </p>
      </div>
      {children}
    </section>
  );
}
