/**
 * 개요의 작업자용 카드(라벨러·검수자). 이어서 할 일을 그린다.
 *
 * 작업자가 아침에 개요를 여는 이유는 이어서 할 일이다. 그래서 보유 중인 작업 수를 가장 크게,
 * 받을 수 있는 작업 수를 옆에 작게 둔다. `내 작업 열기`는 이동만 한다.
 * 라벨링·검수 두 카드가 같은 모양(`QueueCard`)을 쓴다.
 * 카드에 다음 작업 버튼을 합치지 않는다. 다음 작업은 `내 작업`의 카드 하나가 맡는다.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { buttonClasses } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Skeleton } from "@/shared/ui/Skeleton";
import { fetchReviewWaiting, fetchWorkWaiting, meKeys } from "@/features/me";
import { isForbidden, type ProjectDTO } from "@/shared/api/client";

/** 수 하나와 단위를 그린다. 단위는 수에 붙여 쓴다(`147건`). 큰 수는 `big`으로 쓴다. */
function Figure({ value, unit, big = false }: { value: number; unit: string; big?: boolean }) {
  return (
    <span>
      <span className={big ? "text-display tabular-nums text-label" : "text-title-1 tabular-nums text-label"}>
        {value.toLocaleString("ko-KR")}
      </span>
      <span className="ml-0.5 text-footnote text-label-3">{unit}</span>
    </span>
  );
}

/**
 * 이어서 할 작업 카드의 모양. 라벨링·검수가 같은 모양을 쓴다.
 *
 * 두 수의 이름을 같은 높이에 두고(`items-start`), 수는 이름 아래에 둔다.
 * 수의 크기가 달라도 이름 줄이 어긋나지 않는다.
 */
function QueueCard({
  id,
  title,
  query,
  held,
  unit,
  availableLabel,
  available,
}: {
  id: string;
  title: string;
  query: { isError: boolean; refetch: () => unknown };
  held: number | null;
  unit: "건" | "개";
  availableLabel: string;
  available: number | null;
}) {
  const ready = held != null && available != null;
  return (
    <Card as="section" aria-labelledby={id} className="px-6 py-5">
      <div className="flex items-center gap-2">
        <h2 id={id} className="text-headline text-label">{title}</h2>
        {(held ?? 0) > 0 && <Chip tone="amber">이어서 할 작업 있음</Chip>}
      </div>
      {query.isError && !ready ? (
        <div className="mt-3">
          <ErrorState inline message="작업 수 조회 실패" onRetry={() => void query.refetch()} />
        </div>
      ) : !ready ? (
        <Skeleton className="mt-4 h-14 w-64" />
      ) : (
        <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-4">
          <dl className="flex items-start gap-8">
            <div>
              <dt className="text-caption font-semibold text-label-2">보유 중</dt>
              <dd className="mt-1 flex items-baseline gap-2">
                <Figure value={held ?? 0} unit={unit} big />
              </dd>
            </div>
            <div className="border-l border-separator pl-8">
              <dt className="text-caption font-semibold text-label-2">{availableLabel}</dt>
              <dd className="mt-1"><Figure value={available ?? 0} unit={unit} /></dd>
            </div>
          </dl>
          <Link to="/my/work" className={buttonClasses("primary", "md", "ml-auto")}>
            내 작업 열기
          </Link>
        </div>
      )}
    </Card>
  );
}

/**
 * 이어서 할 작업 카드를 그린다. 보유 중인 수와 지금 받을 수 있는 수를 보여 주고 `내 작업`으로 보낸다.
 *
 * 수는 `내 작업` 화면과 같은 조회(같은 쿼리 키)다. 서버가 다음 작업과 같은 조건으로 센다.
 * 받기 전에는 수를 그리지 않는다(로딩과 0건을 구분한다).
 */
export function WorkCard({ project, kind }: { project: ProjectDTO; kind: "label" | "review" }) {
  const pid = project.project_id;
  const q = useQuery({
    queryKey: kind === "label" ? meKeys.workWaiting(pid) : meKeys.reviewWaiting(pid),
    queryFn: () => (kind === "label" ? fetchWorkWaiting(pid) : fetchReviewWaiting(pid)),
    refetchInterval: 60_000,
  });
  if (isForbidden(q.error)) return null;
  const d = q.data;
  return (
    <QueueCard
      id={`${kind}-work-title`}
      title={kind === "label" ? "라벨링" : "검수"}
      query={q}
      held={d ? d.held : null}
      unit="건"
      availableLabel="받을 수 있는 작업"
      available={d?.count ?? null}
    />
  );
}
