/**
 * 사람 서랍. 한 사람의 실적과 설정을 탭으로 보여 준다.
 *
 * 멤버 표와 통계 사람 표가 이 서랍을 연다. 어디서 열어도 같은 내용이다.
 * 한 사람의 역할과 실적을 한 서랍에 모은다.
 *
 * 서랍을 여는 화면(멤버·통계 사람 표)은 관리자 전용이고, 서버도 같은 가드(`require_project_admin`)로 막는다.
 * 멤버가 아닌 사람(나간 사람, 멤버가 아닌 최고관리자)은 통계 사람 표에만 행이 있다.
 * 그때는 실적 탭만 보인다.
 */
import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";

import {
  approvalView,
  doesLabeling,
  EDIT_RATE_OVER_NOTE,
  editRateOver,
  editRateView,
  fetchLabelerPerformance,
  fmtDay,
  fmtTime,
  insightsKeys,
  METRICS,
  rejectionView,
  splitUnit,
} from "@/features/insights";
import { Avatar } from "@/shared/ui/Avatar";
import { Button } from "@/shared/ui/Button";
import { Drawer } from "@/shared/ui/Drawer";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Modal } from "@/shared/ui/Modal";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import { Skeleton } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { TabPanel, Tabs } from "@/shared/ui/Tabs";
import { periodSince, type PeriodKey } from "@/shared/ui/PeriodSelect";
import { type ProjectMemberDTO } from "@/shared/api/client";
import { personLabel } from "@/lib/person";
import { projectRoleLabel } from "@/lib/role_label";

import { listMembers, peopleKeys } from "../api";
import { memberStatusLabel } from "../lib/member_status";
import { PersonSettings } from "./PersonSettings";

/** 서랍의 탭 */
export type PersonTab = "perf" | "settings";

/** 실적 탭의 기간 선택지. 통계 사람 표와 같은 기간 키를 쓴다. */
const PERIODS: { value: PeriodKey; label: string }[] = [
  { value: "7d", label: "최근 7일" },
  { value: "30d", label: "최근 30일" },
  { value: "all", label: "전체 기간" },
];

// 실적

/** 실적 항목 하나를 그린다. 이름, 값(단위는 작게), 아래 한 줄(분모·정의)로 쓴다. */
function Cell({ name, value, unit, sub }: { name: string; value: string; unit?: string; sub?: ReactNode }) {
  return (
    <div className="border-t border-separator py-3">
      <dt className="text-caption font-semibold text-label-2">{name}</dt>
      <dd className="mt-1">
        <span className="text-title-1 tabular-nums text-label">{value}</span>
        {unit && value !== "—" && <span className="ml-0.5 text-footnote text-label-3">{unit}</span>}
        {sub && <p className="mt-0.5 text-caption tabular-nums text-label-3">{sub}</p>}
      </dd>
    </div>
  );
}

/**
 * 실적 탭을 그린다. 통계 사람 표와 같은 네 지표(제출·승인율·반려율·수정률)와 보유·최근 활동을 둔다.
 *
 * 수는 통계 사람 표와 같은 조회(`labeler-performance`, 같은 쿼리 키)를 쓴다.
 * 같은 사람의 같은 수를 두 곳이 다르게 그리지 않는다.
 * 보유와 최근 활동은 멤버 상태를 볼 때 쓰는 현재 상태라 역할과 관계없이 보인다.
 * 검수자의 판정 수는 서버가 사람별로 세지 않는다. 그 역할에는 보유와 최근 활동만 보인다.
 */
function PerformanceTab({
  pid,
  userId,
  member,
}: {
  pid: string;
  userId: string;
  member?: ProjectMemberDTO;
}) {
  const [period, setPeriod] = useState<PeriodKey>("30d");
  const since = periodSince(period);
  const perf = useQuery({
    queryKey: insightsKeys.labelerPerf(pid, period),
    queryFn: () => fetchLabelerPerformance(pid, since),
  });

  const row = perf.data?.find((r) => r.user_id === userId);
  const labels = row ? doesLabeling(row) : member ? member.role === "annotator" : false;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <SegmentedControl size="sm" ariaLabel="실적 기간" options={PERIODS} value={period} onChange={setPeriod} />
        <span className="text-caption text-label-3">라벨링 작업 기준</span>
      </div>

      {perf.isError && perf.data == null ? (
        <ErrorState inline message="실적 조회 실패" onRetry={() => void perf.refetch()} />
      ) : perf.data == null ? (
        <Skeleton className="h-48" />
      ) : row == null ? (
        <EmptyState dense message="기간 내 실적 없음" />
      ) : (
        <dl className="grid grid-cols-2 gap-x-6">
          {(() => {
            const edit = editRateView(row);
            // 승인율·반려율의 값과 괄호는 통계 사람 표와 같은 함수로 고른다. 괄호의 분모는 서버가 준 값이다.
            const approval = approvalView(row);
            const rejection = rejectionView(row);
            const [appr, apprU] = splitUnit(approval.text);
            const [rej, rejU] = splitUnit(rejection.text);
            const [ed, edU] = splitUnit(edit.text);
            return (
              <>
                {labels && <Cell name={METRICS.submitted.name} value={row.submitted.toLocaleString("ko-KR")} unit="건" />}
                <Cell name={METRICS.holding.name} value={row.in_flight.toLocaleString("ko-KR")} unit="건" />
                {labels && (
                  <>
                    <Cell
                      name={METRICS.approvalRate.name}
                      value={appr}
                      unit={apprU}
                      sub={approval.sub}
                    />
                    <Cell
                      name={METRICS.rejectionRate.name}
                      value={rej}
                      unit={rejU}
                      sub={rejection.sub}
                    />
                    <Cell
                      name={METRICS.editRate.name}
                      value={ed}
                      unit={edU}
                      sub={
                        edit.kind === "rate"
                          ? `교정 ${row.edit_distance.toLocaleString("ko-KR")}자 / 초안 ${row.draft_chars.toLocaleString("ko-KR")}자 · ${edit.measured.toLocaleString("ko-KR")}건`
                          : undefined
                      }
                    />
                  </>
                )}
                <Cell
                  name="최근 활동"
                  value={row.last_activity ? fmtDay(row.last_activity) : "—"}
                  sub={row.last_activity ? fmtTime(row.last_activity) : undefined}
                />
              </>
            );
          })()}
        </dl>
      )}

      {!labels && perf.data != null && (
        <p className="text-footnote text-label-3">
          이 역할의 판정 수는 사람별로 집계하지 않습니다. 보유와 최근 활동만 보입니다.
        </p>
      )}

      {labels && row != null && editRateOver(row.edit_rate) && (
        <p className="flex gap-1.5 text-caption text-label-3">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {EDIT_RATE_OVER_NOTE}
        </p>
      )}
    </div>
  );
}

// 서랍

/**
 * 서랍의 머리와 탭, 탭 본문을 그린다. `PersonDrawer`가 쓴다.
 *
 * Args:
 *   userId: 사람. 멤버 목록에 없으면 실적 탭만 보인다.
 *   fallbackName: 멤버 목록에 없는 사람의 이름·이메일(통계 사람 행에서 온다).
 *   onDirtyChange: 설정 탭에 저장하지 않은 변경이 생기거나 없어질 때 부르는 함수.
 *   onGone: 멤버를 제거했을 때 부르는 함수.
 */
export function PersonPanel({
  pid,
  userId,
  initialTab = "perf",
  fallbackName,
  onDirtyChange,
  onGone,
}: {
  pid: string;
  userId: string;
  initialTab?: PersonTab;
  fallbackName?: { name?: string | null; email?: string | null };
  onDirtyChange?: (dirty: boolean) => void;
  onGone?: () => void;
}) {
  const [tab, setTab] = useState<PersonTab>(initialTab);
  const members = useQuery({
    queryKey: peopleKeys.members(pid),
    queryFn: () => listMembers(pid),
  });
  // 보유 수는 기간과 관계없는 현재 상태라 전체 기간 조회에서 읽는다. 통계 사람 표와 같은 키다.
  const perfAll = useQuery({
    queryKey: insightsKeys.labelerPerf(pid, "all"),
    queryFn: () => fetchLabelerPerformance(pid),
  });
  const member = members.data?.find((m) => m.user_id === userId);
  const holding = perfAll.data?.find((r) => r.user_id === userId)?.in_flight ?? null;
  const dirtyCb = useCallback((d: boolean) => onDirtyChange?.(d), [onDirtyChange]);

  const tabs = [
    { key: "perf", label: "실적" },
    ...(member ? [{ key: "settings", label: "설정" }] : []),
  ];
  const active = tabs.some((t) => t.key === tab) ? tab : "perf";

  const name = member
    ? personLabel(member.display_name, member.email)
    : personLabel(fallbackName?.name, fallbackName?.email);
  const email = member?.email ?? fallbackName?.email;

  return (
    <div>
      <div className="flex items-start gap-3">
        <Avatar name={name} size={40} />
        <div className="min-w-0 flex-1">
          {email && <p className="truncate text-footnote text-label-3">{email}</p>}
          {member && (
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-label-2">
              {/* 나란한 속성은 다른 화면처럼 가운뎃점으로 나눈다. */}
              <span className="font-semibold">{projectRoleLabel(member.role)}</span>
              <span className="text-label-3" aria-hidden="true">·</span>
              <StatusBadge status={member.status} label={memberStatusLabel(member.status)} />
            </p>
          )}
          {!member && members.data && <p className="mt-1 text-footnote text-label-3">이 프로젝트의 멤버가 아닙니다.</p>}
        </div>
      </div>

      <Tabs
        className="-mx-6 mt-4 px-6"
        variant="underline"
        idPrefix="person"
        tabs={tabs}
        value={active}
        onChange={(k) => setTab(k as PersonTab)}
      />

      <div className="pt-5">
        <TabPanel idPrefix="person" tabKey="perf" className={active === "perf" ? "" : "hidden"}>
          {active === "perf" && <PerformanceTab pid={pid} userId={userId} member={member} />}
        </TabPanel>
        {member && (
          /* 설정 탭은 탭을 옮겨도 입력이 남도록 늘 그려 두고 숨긴다. */
          <TabPanel idPrefix="person" tabKey="settings" className={active === "settings" ? "" : "hidden"}>
            <PersonSettings
              pid={pid}
              member={member}
              holding={holding}
              onDirtyChange={dirtyCb}
              onGone={() => onGone?.()}
            />
          </TabPanel>
        )}
      </div>
    </div>
  );
}

/**
 * 사람 서랍을 연다. 설정 탭에 저장하지 않은 변경이 있으면 닫기 전에 확인한다.
 *
 * Args:
 *   userId: 연 사람. `null`이면 닫힌 상태다.
 *   fallbackName: 멤버 목록에 없는 사람의 이름(통계 사람 행에서 넘긴다).
 */
export function PersonDrawer({
  pid,
  userId,
  onClose,
  initialTab,
  fallbackName,
}: {
  pid: string;
  userId: string | null;
  onClose: () => void;
  initialTab?: PersonTab;
  fallbackName?: { name?: string | null; email?: string | null };
}) {
  const [dirty, setDirty] = useState(false);
  const [asking, setAsking] = useState(false);
  const members = useQuery({
    queryKey: peopleKeys.members(pid),
    queryFn: () => listMembers(pid),
    enabled: !!userId,
  });
  const member = members.data?.find((m) => m.user_id === userId);
  const name = member
    ? personLabel(member.display_name, member.email)
    : personLabel(fallbackName?.name, fallbackName?.email);

  const close = () => {
    setDirty(false);
    setAsking(false);
    onClose();
  };
  const tryClose = () => (dirty ? setAsking(true) : close());

  return (
    <>
      <Drawer open={userId != null} onClose={tryClose} title={name} className="max-w-[480px]">
        {userId != null && (
          <PersonPanel
            key={userId}
            pid={pid}
            userId={userId}
            initialTab={initialTab}
            fallbackName={fallbackName}
            onDirtyChange={setDirty}
            onGone={close}
          />
        )}
      </Drawer>
      <Modal
        open={asking}
        onClose={() => setAsking(false)}
        title="저장하지 않은 변경"
        size="sm"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setAsking(false)}>계속 편집</Button>
            <Button variant="danger" size="sm" onClick={close}>버리고 닫기</Button>
          </>
        }
      >
        <p className="text-body text-label">{name}의 설정에 저장하지 않은 변경이 있습니다.</p>
        <p className="mt-2 text-footnote text-label-3">닫으면 바꾼 값이 사라집니다.</p>
      </Modal>
    </>
  );
}
