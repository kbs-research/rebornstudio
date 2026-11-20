/**
 * 통계 화면 위의 기간 합계 칸. 제출·판정·승인율·반려율·수정률을 프로젝트 단위로 한 줄에 둔다.
 *
 * 수는 서버의 기간 합계(`labeler-performance/summary`)이고 아래 사람별 표의 행을 더한 값과 같다.
 * 칸에 그릴 값은 `lib/summary`가 고른다. 프로젝트 합계도 사람 단위 수처럼 판정 색을 칠하지 않는다.
 * 기간은 페이지 머리의 `PeriodSelect`가 주소(`?period=`)에 두고, 이 칸과 사람별 표가 같은 값을 읽는다.
 */
import { useQuery } from "@tanstack/react-query";

import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { periodLabel, periodSince, usePeriod } from "@/shared/ui/PeriodSelect";
import { Skeleton } from "@/shared/ui/Skeleton";
import { DefinedTerm } from "@/shared/ui/Tooltip";

import { fetchLabelerSummary, insightsKeys } from "../api";
import { METRICS } from "../lib/metrics";
import { summaryCells, type SummaryCell } from "../lib/summary";

/** 요약 칸 이름. 받기 전에도 칸의 뼈대를 그려 받은 뒤 줄이 다시 배치되지 않게 한다. 순서는 `summaryCells`와 같다. */
const NAMES = [
  METRICS.submitted.name,
  METRICS.decided.name,
  METRICS.approvalRate.name,
  METRICS.rejectionRate.name,
  METRICS.editRate.name,
];

/** 칸 하나를 그린다. 이름(정의 툴팁), 큰 값과 작은 단위, 아래 분모 줄 순서다. */
function SummaryTile({ cell }: { cell: SummaryCell }) {
  return (
    <Card className="flex min-w-0 flex-col gap-1.5 px-4 py-3.5">
      <dt className="text-footnote text-label-2">
        <DefinedTerm define={cell.define}>{cell.name}</DefinedTerm>
      </dt>
      <dd className="flex min-w-0 flex-col gap-1">
        <span className="flex items-baseline gap-1">
          <span className="text-title-1 tabular-nums text-label">{cell.value}</span>
          {cell.unit && <span className="text-caption text-label-3">{cell.unit}</span>}
        </span>
        <span className="text-caption tabular-nums text-label-3">{cell.sub}</span>
      </dd>
    </Card>
  );
}

/** 불러오는 중인 칸을 그린다. 이름은 그대로 두고 값만 스켈레톤으로 바꾼다. */
function SummaryTileSkeleton({ name }: { name: string }) {
  return (
    <Card className="flex min-w-0 flex-col gap-1.5 px-4 py-3.5">
      <dt className="text-footnote text-label-2">{name}</dt>
      <dd className="flex flex-col gap-1">
        <Skeleton className="h-7 w-16" />
        <Skeleton className="h-3.5 w-24" />
      </dd>
    </Card>
  );
}

/**
 * 기간 합계 칸 다섯 개를 그린다.
 *
 * Args:
 *   pid: 프로젝트 ID. 페이지가 주소의 프로젝트를 찾은 뒤에 넘긴다.
 */
export function PeriodSummary({ pid }: { pid: string }) {
  const [period] = usePeriod();
  const since = periodSince(period);
  const summary = useQuery({
    // 기간을 쿼리 키에 넣는다. 넣지 않으면 기간을 바꿔도 캐시된 이전 합계가 그대로 보인다.
    queryKey: insightsKeys.labelerSummary(pid, period),
    queryFn: () => fetchLabelerSummary(pid, since),
  });

  /* 실패를 0건으로 그리지 않는다. 0으로 그리면 서버 오류가 일이 없었던 기간처럼 읽힌다. */
  if (summary.isError && summary.data == null) {
    return (
      <section aria-label="기간 합계">
        <ErrorState inline message="기간 합계 조회 실패" onRetry={() => void summary.refetch()} />
      </section>
    );
  }

  return (
    <section aria-label="기간 합계">
      {/* 지원 폭(1280px 이상)에서는 다섯 칸이 한 줄에 들어간다. 좁아지면 줄을 나눠 값이 잘리지 않게 한다. */}
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {summary.data == null
          ? NAMES.map((name) => <SummaryTileSkeleton key={name} name={name} />)
          : summaryCells(summary.data, periodLabel(period)).map((cell) => <SummaryTile key={cell.key} cell={cell} />)}
      </dl>
    </section>
  );
}
