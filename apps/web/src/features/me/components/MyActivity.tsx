/**
 * `내 활동`(`/my/activity`) 본문. 본인이 한 일의 기록을 보여 준다.
 *
 * 라벨러·관리자에게는 본인의 라벨링 기록 표와 기록 상세 서랍을 보여 준다.
 * 검수자에게는 본인이 내린 판정 목록을 보여 준다.
 * 기록은 서버 응답(`me/work-history`, `me/decisions`)이다. 화면은 기록을 보여 주기만 하고 좋다·나쁘다를 판정하지 않는다.
 */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ReadOnlyLabel } from "@/components/ReadOnlyLabel";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { Drawer } from "@/shared/ui/Drawer";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { FilterMenu } from "@/shared/ui/FilterMenu";
import { IdChip } from "@/shared/ui/IdChip";
import { Skeleton } from "@/shared/ui/Skeleton";
import { fmtClipRange, fmtSecKo } from "@/shared/lib/fmtSecs";
import { fmtDateTime, fmtFullDateTime, whenLabel } from "@/shared/lib/time";

import {
  fetchMyDecisions,
  fetchMyWorkHistory,
  fetchMyWorkHistoryDetail,
  meKeys,
} from "../api";
import {
  MY_STATE,
  MY_STATE_FILTER,
  VERDICT_TONE,
  historyCountLabel,
  verdictAddsInfo,
  verdictLabel,
  type MyWorkHistoryRow,
  type MyWorkState,
} from "../lib/my_history";
import { decisionCountLabel, decisionTarget, decisionView } from "../lib/decisions";

/** 본인 라벨링 기록을 한 번에 받는 행 수. 커서로 이어 받는다. */
const HISTORY_PAGE = 50;

/** 상세에 쓰는 절대 시각(`9월 26일 10:02`)을 만든다. 없으면 `—`다. */
function fmtAt(iso: string | null | undefined): string {
  return iso ? fmtDateTime(iso) : "—";
}

// 기록 표

/*
  열은 본인이 한 일을 그린다(`my_*`). 작업의 현재 값은 작업이 다른 라벨러에게 넘어가면 그 사람의 값이 된다.
  첫 열은 영상 제목과 클립 구간이다. 작업 ID 앞자리만으로는 어느 작업인지 떠올릴 수 없다.
  열은 정렬하지 않는다(`sortable: false`). 서버가 최신순으로 잇는 커서 목록이라, 불러온 행만 정렬하면
  "가장 오래된 기록"이 불러온 행 안에서 고른 기록이 된다.
*/
const WORK_HISTORY_COLUMNS: DataColumn<MyWorkHistoryRow>[] = ([
  {
    label: "작업",
    kind: "title",
    value: (w) => w.video_title ?? w.work_item_id,
    cell: (w) => (
      <div className="min-w-0">
        <span className="block max-w-[20rem] truncate font-medium text-label" title={w.video_title ?? undefined}>
          {w.video_title ?? "제목 없음"}
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 text-caption text-label-2">
          {w.clip_start_seconds != null && w.clip_end_seconds != null && (
            <span className="font-mono tabular-nums">{fmtClipRange(w.clip_start_seconds, w.clip_end_seconds)}</span>
          )}
          <IdChip id={w.work_item_id} label="작업 ID" />
        </span>
      </div>
    ),
  },
  {
    label: "내 제출",
    define: "내가 이 작업을 마지막으로 제출한 시각입니다. 반려 후 다시 제출했으면 제출 횟수를 같이 씁니다.",
    kind: "date",
    align: "right",
    width: "7.5rem",
    value: (w) => w.my_submitted_at ?? null,
    cell: (w) =>
      w.my_submitted_at ? (
        <span className="inline-flex flex-col items-end" title={fmtFullDateTime(w.my_submitted_at)}>
          <span>{whenLabel(w.my_submitted_at)}</span>
          {w.my_submit_count > 1 && <span className="text-caption text-label-3">{w.my_submit_count}회 제출</span>}
        </span>
      ) : (
        "—"
      ),
  },
  {
    label: "내 작업 시간",
    define: "내가 받은 시각부터 제출까지 걸린 시간입니다. 반려로 돌아온 작업은 반려 시각부터 잽니다.",
    kind: "number",
    align: "right",
    width: "7rem",
    value: (w) => w.my_work_seconds ?? null,
    cell: (w) => fmtSecKo(w.my_work_seconds),
  },
  {
    label: "상태",
    define: "이 작업이 지금 나에게 어떤 상태인지입니다. 신고나 멤버 변경으로 보유가 풀린 뒤 다른 작업자가 받으면 다른 작업자에게 넘어감으로 표시합니다.",
    kind: "status",
    width: "12rem",
    value: (w) => MY_STATE[w.my_state]?.label ?? w.my_state,
    cell: (w) => (
      <Chip variant="dot" tone={MY_STATE[w.my_state]?.tone ?? "quiet"}>
        {MY_STATE[w.my_state]?.label ?? w.my_state}
      </Chip>
    ),
  },
  {
    label: "검수 결과",
    define: "내 마지막 제출의 판정입니다. 반려면 사유를 같이 씁니다. 판정 전이거나 상태와 같은 말이면 비워 둡니다.",
    kind: "status",
    width: "14rem",
    value: (w) => (w.my_verdict ? verdictLabel(w.my_verdict) : null),
    cell: (w) => {
      // 상태 열에 이미 나온 것(검수 대기, 승인)은 비워 두 열에 같은 내용이 반복되지 않게 한다(`lib/my_history`).
      if (!w.my_verdict || !verdictAddsInfo(w)) return null;
      const reasons = (w.my_reasons ?? []).map((r) => r.label).join(" · ");
      return (
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <Chip variant="dot" tone={VERDICT_TONE[w.my_verdict]}>{verdictLabel(w.my_verdict)}</Chip>
          {reasons && (
            <span className="max-w-[8rem] truncate text-footnote text-label-2" title={reasons}>
              {reasons}
            </span>
          )}
        </span>
      );
    },
  },
] satisfies DataColumn<MyWorkHistoryRow>[]).map((c) => ({ ...c, sortable: false }));

// 기록 상세

/**
 * 라벨링 기록 상세 서랍을 그린다. 본인의 제출·작업 시간, 낸 캡션, 그 검수 결과를 보여 준다.
 *
 * 본인 기록 전용 조회(`/me/work-history/{id}`)를 쓴다. `/work/{id}/labels`는 지금 보유한 사람과
 * 검수자에게만 열려서, 다른 라벨러에게 넘어간 작업에서 403이다.
 */
function HistoryDrawer({
  projectId,
  row,
  onClose,
}: {
  projectId: string;
  row: MyWorkHistoryRow | null;
  onClose: () => void;
}) {
  // 쿼리 키는 목록 키(`["my-work-history", pid, …]`) 아래에 둔다. 목록을 무효화하면 상세도 다시 받는다.
  const detail = useQuery({
    queryKey: meKeys.workHistoryDetail(projectId, row?.work_item_id),
    queryFn: () => fetchMyWorkHistoryDetail(projectId, row!.work_item_id),
    enabled: !!row,
    meta: { handled: true },
  });
  const r = detail.data?.row ?? row;
  const labels = detail.data?.labels ?? [];
  const latest = labels.length ? labels[labels.length - 1] : undefined;
  const reviews = (detail.data?.reviews ?? []).slice().sort((a, b) => +new Date(b.reviewed_at) - +new Date(a.reviewed_at));
  const moved = r?.my_state === "handed_over" || r?.my_state === "returned";

  const row2 = (label: string, value: ReactNode) => (
    <>
      <dt className="text-label-2">{label}</dt>
      <dd className="min-w-0 tabular-nums text-label">{value}</dd>
    </>
  );

  return (
    <Drawer open={!!row} onClose={onClose} title="라벨링 기록 상세" className="max-w-[480px]">
      {r && (
        <div className="space-y-5">
          {/* 영상과 구간은 제목 아래 한 줄로 쓴다. 정의 목록에서 되풀이하지 않는다. */}
          <p className="-mt-2 truncate text-footnote text-label-2" title={r.video_title ?? undefined}>
            {r.video_title ?? "제목 없음"}
            <span aria-hidden="true" className="px-1.5 text-label-3">·</span>
            <span className="font-mono tabular-nums">{fmtClipRange(r.clip_start_seconds, r.clip_end_seconds)}</span>
          </p>
          <dl className="grid grid-cols-[6.5rem_1fr] items-center gap-y-2.5 text-footnote">
            {row2(
              "상태",
              <Chip variant="dot" tone={MY_STATE[r.my_state]?.tone ?? "quiet"}>
                {MY_STATE[r.my_state]?.label ?? r.my_state}
              </Chip>,
            )}
            {row2("작업 ID", <IdChip id={r.work_item_id} label="작업 ID" />)}
            {row2("작업 시작", fmtAt(r.my_started_at))}
            {row2(
              "내 제출",
              <>
                {fmtAt(r.my_submitted_at)}
                {r.my_submit_count > 1 && <span className="ml-1.5 text-label-2">{r.my_submit_count}회 제출</span>}
              </>,
            )}
            {row2("내 작업 시간", fmtSecKo(r.my_work_seconds))}
          </dl>
          {/* 결과와 안내를 나눈다. 굵은 결과 한 줄과 작은 안내 한 줄이다. */}
          {moved && (
            <div className="rounded-card bg-canvas-2 px-3.5 py-2.5 text-footnote">
              <p className="font-semibold text-label">
                {r.my_state === "handed_over" ? "이 작업은 다른 작업자에게 넘어갔습니다." : "이 작업은 대기열로 돌아갔습니다."}
              </p>
              <p className="mt-0.5 text-label-2">내가 낸 캡션과 그 검수 결과만 보입니다.</p>
            </div>
          )}
          {detail.isError ? (
            // 불러오지 못한 것을 "제출 캡션 없음"으로 표시하지 않는다.
            <ErrorState inline message="기록 상세 조회 실패" onRetry={() => void detail.refetch()} />
          ) : (
            <>
              <section className="border-t border-separator pt-4">
                <h3 className="mb-2 text-callout font-semibold text-label">내가 제출한 캡션</h3>
                {detail.isLoading ? (
                  <div role="status" aria-label="캡션 불러오는 중" className="space-y-2">
                    <Skeleton className="h-4 w-2/3" />
                    <Skeleton className="h-4 w-1/2" />
                  </div>
                ) : !latest ? (
                  <p className="text-footnote text-label-2">제출한 캡션 없음</p>
                ) : (
                  <ReadOnlyLabel
                    data={latest.label_data}
                    captionTitle={false}
                  />
                )}
              </section>
              {reviews.length > 0 && (
                <section className="border-t border-separator pt-4">
                  <h3 className="mb-2 text-callout font-semibold text-label">검수 결과</h3>
                  <ul className="space-y-2">
                    {reviews.map((v) => (
                      <li key={v.review_id} className="rounded-card border border-separator px-3.5 py-3">
                        <div className="flex items-center justify-between gap-2">
                          <Chip variant="dot" tone={VERDICT_TONE[v.verdict] ?? "neutral"}>{verdictLabel(v.verdict)}</Chip>
                          <time dateTime={v.reviewed_at} className="shrink-0 text-caption tabular-nums text-label-2">
                            {fmtAt(v.reviewed_at)}
                          </time>
                        </div>
                        {/* 사유 칩은 서버가 코드와 이름으로 준다. 보충 문장과 따로 그린다. */}
                        {(v.reasons?.length ?? 0) > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {v.reasons!.map((t) => (
                              <Chip key={t.code} tone="bad">{t.label}</Chip>
                            ))}
                          </div>
                        )}
                        {v.comment && <p className="mt-2 whitespace-pre-wrap text-footnote text-label">{v.comment}</p>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}

// 본문

/** 본인 판정 목록을 한 번에 받는 행 수. 나머지는 `더 보기`로 받는다. */
const DECISION_PAGE = 30;

/**
 * 검수자 본인이 내린 판정 목록을 그린다.
 *
 * 반려 사유와 메모까지 같은 줄에 두어 자기 판정을 다시 읽고, 이의가 오면 근거를 찾게 한다.
 * 머리의 수는 서버가 센 전체 수다.
 */
function MyDecisions({ projectId }: { projectId: string }) {
  const q = useInfiniteQuery({
    queryKey: meKeys.myDecisions(projectId),
    queryFn: ({ pageParam }) => fetchMyDecisions(projectId, DECISION_PAGE, pageParam),
    initialPageParam: undefined as { before: string; beforeId: string } | undefined,
    getNextPageParam: (last) => {
      const tail = last.items[last.items.length - 1];
      return last.items.length < DECISION_PAGE || !tail ? undefined : { before: tail.decided_at, beforeId: tail.decision_id };
    },
    meta: { handled: true },
  });
  const rows = (q.data?.pages ?? []).flatMap((p) => p.items);
  const total = q.data?.pages[0]?.total ?? 0;
  return (
    <Card as="section" aria-label="내 판정 목록" className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-separator px-5 py-3">
        <h2 className="text-headline text-label">내 판정 목록</h2>
        {q.data && total > 0 && <Chip>{decisionCountLabel(rows.length, total)} · 최신순</Chip>}
      </div>
      {q.isError && !q.data ? (
        <ErrorState inline message="판정 목록 조회 실패" onRetry={() => void q.refetch()} />
      ) : !q.data ? (
        <div role="status" aria-label="판정 목록 불러오는 중" className="space-y-2 px-5 py-4">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState dense message="내린 판정 없음" />
      ) : (
        <ul className="divide-y divide-separator">
          {rows.map((r) => {
            const v = decisionView(r);
            return (
              <li key={r.decision_id} className="grid grid-cols-[7rem_minmax(0,1fr)_auto] items-start gap-4 px-5 py-3">
                <span className="flex flex-wrap items-center gap-1.5">
                  <Chip variant="dot" tone={v.tone}>{v.label}</Chip>
                </span>
                <div className="min-w-0">
                  <p className="truncate text-callout text-label" title={decisionTarget(r)}>{decisionTarget(r)}</p>
                  {(r.reasons?.length ?? 0) > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {r.reasons!.map((t) => (
                        <Chip key={t.code} tone="bad">{t.label}</Chip>
                      ))}
                    </div>
                  )}
                  {r.comment && <p className="mt-1.5 line-clamp-2 text-footnote text-label-2" title={r.comment}>{r.comment}</p>}
                </div>
                <time dateTime={r.decided_at} title={fmtFullDateTime(r.decided_at)} className="shrink-0 text-caption tabular-nums text-label-3">
                  {fmtAt(r.decided_at)}
                </time>
              </li>
            );
          })}
        </ul>
      )}
      {q.hasNextPage && (
        <div className="border-t border-separator px-5 py-3">
          <Button variant="secondary" size="sm" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
            {q.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
          </Button>
        </div>
      )}
      {q.isError && q.data != null && (
        <ErrorState inline message="판정 목록 조회 실패" onRetry={() => void q.fetchNextPage()} />
      )}
    </Card>
  );
}

/**
 * `내 활동` 본문을 그린다. 라벨을 내는 역할(라벨러·관리자)과 검수자만 그린다.
 *
 * `role`은 역할을 확인한 뒤에 넘긴다(`pages/my_activity.tsx`). 확인 전에 그리면 라벨러 화면이
 * 먼저 그려지고 라벨러용 요청이 나간다.
 */
export function ActivityBody({ projectId, role }: { projectId: string; role: string | null | undefined }) {
  const isReviewer = role === "reviewer";
  // 관리자는 실제로 라벨링을 할 수 있어 라벨러와 같은 기록을 본다.
  const isLabeler = role === "annotator" || role === "project_admin";

  /*
    기록을 끊지 않고 이어 받는다. 검색과 상태 거르기는 서버가 전체 기록에 적용한다.
    최신 몇 건에서 알리지 않고 끊으면 표가 분모 없이 그만큼만 보여 준다.
  */
  const [historyQ, setHistoryQ] = useState("");
  const [historyApplied, setHistoryApplied] = useState("");
  const [historyState, setHistoryState] = useState<MyWorkState | "">("");
  const historyQuery = useInfiniteQuery({
    queryKey: meKeys.workHistory(projectId, historyApplied, historyState),
    queryFn: ({ pageParam }) =>
      fetchMyWorkHistory(projectId, { limit: HISTORY_PAGE, q: historyApplied, state: historyState, before: pageParam }),
    enabled: !!projectId && isLabeler,
    initialPageParam: undefined as { at: string; id: string } | undefined,
    getNextPageParam: (last) => {
      const tail = last[last.length - 1];
      if (last.length < HISTORY_PAGE || !tail) return undefined;
      // 커서는 서버가 정렬에 쓴 시각이다(`history_at`).
      return { at: tail.history_at, id: tail.work_item_id };
    },
    meta: { handled: true },
  });
  const historyRows = (historyQuery.data?.pages ?? []).flat();
  const narrowed = !!historyApplied || !!historyState;

  const [detail, setDetail] = useState<MyWorkHistoryRow | null>(null);

  if (isReviewer) {
    return <MyDecisions projectId={projectId} />;
  }

  if (!isLabeler) {
    // 이 탭을 보지 않는 역할이다. 주소로 직접 와도 없는 표를 그리지 않는다.
    return <EmptyState message="활동 기록 없음" />;
  }

  return (
    <div className="space-y-6">
      <Card as="section" aria-label="내 라벨링 기록" className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-separator px-5 py-3">
          <h2 className="text-headline text-label">내 라벨링 기록</h2>
          {/* 분모를 밝힌다. 뒤에 더 있으면 "전체"라고 쓰지 않는다. */}
          {!historyQuery.isLoading && (
            <Chip>{historyCountLabel(historyRows.length, !!historyQuery.hasNextPage, narrowed)}</Chip>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <FilterMenu
              label="상태"
              value={historyState}
              options={MY_STATE_FILTER}
              onChange={(v) => setHistoryState(v as MyWorkState | "")}
              align="right"
            />
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setHistoryApplied(historyQ.trim());
              }}
            >
              <label className="relative">
                <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-label-3" />
                <input
                  type="search"
                  value={historyQ}
                  onChange={(e) => setHistoryQ(e.target.value)}
                  placeholder="영상 제목 또는 작업 ID"
                  aria-label="영상 제목이나 작업 ID로 검색"
                  className="h-8 w-60 rounded-control border border-separator-strong bg-panel pl-8 pr-2.5 text-footnote text-label outline-none placeholder:text-placeholder focus:border-focus focus:ring-2 focus:ring-focus/25"
                />
              </label>
              <Button size="sm" variant="secondary" type="submit">검색</Button>
            </form>
          </div>
        </div>
        <DataTable<MyWorkHistoryRow>
          bare
          columns={WORK_HISTORY_COLUMNS}
          rows={historyRows}
          rowKey={(w) => w.work_item_id}
          onRowClick={(w) => setDetail(w)}
          rowClassName={(w) => (detail?.work_item_id === w.work_item_id ? "bg-amber-soft" : "")}
          loading={historyQuery.isLoading}
          error={historyQuery.isError}
          onRetry={() => void historyQuery.refetch()}
          pageSize={0}
          empty={
            narrowed ? (
              <EmptyState
                message="조건에 맞는 기록 없음"
                action={
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setHistoryQ("");
                      setHistoryApplied("");
                      setHistoryState("");
                    }}
                  >
                    조건 지우기
                  </Button>
                }
              />
            ) : (
              <EmptyState message="작업 기록 없음" />
            )
          }
          footer={
            <div className="flex w-full items-center justify-between gap-3">
              <span className="text-footnote text-label-2">제출했거나 보유 중인 작업만 · 최신순</span>
              <span className="flex items-center gap-2">
                {narrowed && historyRows.length > 0 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setHistoryQ("");
                      setHistoryApplied("");
                      setHistoryState("");
                    }}
                  >
                    조건 지우기
                  </Button>
                )}
                {historyQuery.hasNextPage && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void historyQuery.fetchNextPage()}
                    disabled={historyQuery.isFetchingNextPage}
                  >
                    {historyQuery.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                  </Button>
                )}
              </span>
            </div>
          }
        />
      </Card>

      <HistoryDrawer
        projectId={projectId}
        row={detail}
        onClose={() => setDetail(null)}
      />

    </div>
  );
}
