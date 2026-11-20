/**
 * 통계 화면의 사람별 실적 패널.
 *
 * 사람별 표(DataTable 정렬·검색)와 행을 누르면 여는 사람 서랍(`features/people`의 `PersonDrawer`)으로 구성된다.
 * 열은 처리량(제출)·승인율·반려율·수정률이다. 보유 중과 최근 활동은 멤버 표가 보여 준다.
 * 지표 이름과 정의는 `lib/metrics`에서 가져온다. 사람 단위 수에는 판정 색을 쓰지 않는다.
 *
 * 이 패널은 기간이 걸린다. 페이지 머리의 `PeriodSelect`가 주소에 둔 기간을 읽어 `?since=`로 서버에 넘긴다.
 * 위 합계 칸과 같은 기간을 읽으므로 기간 선택을 패널 안에 따로 두지 않는다.
 * 기간으로 자르는 것은 서버다. 화면이 자르면 페이지에 불러온 행만 센다.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { Avatar } from "@/shared/ui/Avatar";
import { Button } from "@/shared/ui/Button";
import { Chip } from "@/shared/ui/Chip";
import { type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PersonDrawer } from "@/features/people";
import { labelingRows } from "../lib/labeling_roles";
import { approvalView, rejectionView, type RateView } from "../lib/rates";
import { periodLabel, periodSince, usePeriod } from "@/shared/ui/PeriodSelect";
import { projectRoleLabel } from "@/lib/role_label";
import { TableCard } from "@/shared/ui/TableCard";
import { personLabel } from "@/lib/person";
import { DefinedTerm } from "@/shared/ui/Tooltip";
import { editRateDefine, editRateFraction, editRateNote, editRateView } from "../lib/edit_rate";
import { metricDefine, METRICS } from "../lib/metrics";
import { type LabelerPerfRow } from "@/shared/api/client";
import { ProjectShellState, useProjects } from "@/features/projects";

import { fetchLabelerPerformance, insightsKeys } from "../api";

// 제출 인라인 바

/** 제출 막대를 그린다. 이 표에서 가장 많이 제출한 사람 대비 비율이다. */
function ThroughputBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  /*
    열 폭을 실제로 정하는 것은 `min-w`다. `<th>`의 `width`는 `table-layout: auto`에서 권장값이라 내용이 더 넓으면 내용 폭을 따른다.
    막대는 비율만 보여 주므로 3.5rem이면 충분하다(0%와 100%가 구별되면 된다).
    `min-w`를 크게 두면 열 폭을 줄여도 줄지 않는다.
  */
  return (
    <div className="min-w-[3.5rem] w-full">
      <div className="h-1.5 rounded-capsule bg-fill-2">
        <div className="h-1.5 rounded-capsule bg-accent transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** 비율 칸을 그린다. 값이 없으면 값 라벨만, 있으면 값과 그 아래 분자·분모 괄호를 그린다. */
function RateCell({ view }: { view: RateView }) {
  if (view.kind !== "rate") return <span className="text-label-3">{view.text}</span>;
  /* 분모를 값 바로 아래에 둔다. `87%`만으로는 두 건 중 하나인지 백 건 중 여든일곱인지 알 수 없다.
     값 옆에 이으면 열 폭이 분모의 자릿수를 따라 커져 `100% (1,234/1,234)`일 때 1280px(지원 하한)에서 표가 넘치므로 아래에 둔다.
     `사람` 열이 이미 두 줄이라 행 높이는 늘지 않는다. */
  return (
    <span className="inline-flex flex-col items-end gap-0.5 tabular-nums">
      <span>{view.text}</span>
      <span className="text-caption text-label-3">{view.sub}</span>
    </span>
  );
}

// 메인 컴포넌트

/** 사람별 라벨링 표와 상세 드로어를 그린다. */
export function LabelerPerformancePanel() {
  const { slug } = useParams<{ slug: string }>();

  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);

  const [period] = usePeriod();
  const since = periodSince(period);

  // 누른 행. 상세 드로어에 넘기고, null이면 드로어를 닫는다.
  const [selected, setSelected] = useState<LabelerPerfRow | null>(null);
  // 라벨링하지 않는 멤버(검수자)까지 보면 true. 기본은 숨긴다.
  const [showAll, setShowAll] = useState(false);

  const perf = useQuery({
    // 기간을 쿼리 키에 넣는다. 넣지 않으면 기간을 바꿔도 캐시된 이전 수가 그대로 보인다.
    queryKey: insightsKeys.labelerPerf(project?.project_id, period),
    queryFn: () => fetchLabelerPerformance(project!.project_id, since),
    enabled: !!project,
  });

  if (projects.isLoading) return <PageSkeleton table />;
  /*
    실패를 "없음"으로 표시하지 않는다. 공용 `ProjectShellState`가 실패와 없음을 나눠 그리고 다시 시도를 준다.
    직접 그리면 목록 조회가 500이어도 `프로젝트를 찾을 수 없습니다`가 떠서 서버 오류가 주소 오류처럼 읽힌다.
    이 위치는 페이지 대신 패널 하나라 `dense`를 쓴다. 페이지 머리와 탭은 그대로 있고 옆 패널도 같은 상태라, 크게 그리면 화면이 빈 상자로 덮인다.
  */
  if (!project)
    return <ProjectShellState dense isError={projects.isError} onRetry={() => void projects.refetch()} />;

  const rows = perf.data ?? [];
  const maxSubmitted = Math.max(1, ...rows.map((r) => r.submitted));
  /*
    표는 라벨링하는 사람만 기본으로 싣는다. 숨긴 수는 표 머리에 밝히고 토글로 모두 보여 준다.
    숨기지 않으면 검수자처럼 구조적으로 0인 행이 실제 라벨러 사이에 섞인다.
  */
  const { shown, others } = labelingRows(rows, showAll);

  // DataTable 컬럼 정의

  const columns: DataColumn<LabelerPerfRow>[] = [
    {
      label: "사람",
      kind: "person",
      // 검색·정렬 값에도 id를 쓰지 않는다. 이름과 이메일이 모두 없으면 `이름 없음`이다.
      value: (r) => `${r.name ?? ""} ${r.email ?? ""}`.trim() || personLabel(),
      cell: (r) => (
        <div className="flex items-center gap-2">
          <Avatar name={personLabel(r.name, r.email)} size={28} />
          {/* 이름·이메일 영역에 최대 폭을 둔다.
              넘으면 말줄임으로 자르고 전체 값은 툴팁과 상세 드로어에 둔다.
              이메일 길이는 사람마다 다르고, 표 배치는 그 길이에 흔들리지 않아야 한다.
              자동 폭이면 긴 이메일이 열을 넓혀 비율 열이 좁아진다. */}
          <div className="min-w-0 max-w-[10.5rem]">
            <div className="flex items-center gap-1.5">
              {/* 이름이 없으면 해시 일부 대신 이메일을,
                  이메일도 없으면 `이름 없음`을 쓴다. */}
              <span className="truncate font-medium text-label">
                {personLabel(r.name, r.email)}
              </span>
              {/* 멤버가 아닌데 이 기간에 제출한 사람(최고관리자, 나간 멤버). */}
              {r.role == null && (
                <Chip tone="quiet">멤버 아님</Chip>
              )}
              {/* 역할을 이름 옆에 둔다.
                  이 표는 활성 멤버 전원을 싣지만 라벨링만 세므로, 검수자 행은 모두 `—`가 된다.
                  역할이 없으면 그 `0`이 일을 하지 않았다는 뜻으로 읽혀, 검수를 도맡은 사람도 일하지 않은 것처럼 보인다. */}
              {r.role && (
                <Chip tone="quiet">{projectRoleLabel(r.role)}</Chip>
              )}
            </div>
            {r.email && (
              <div className="truncate text-footnote text-label-3" title={r.email}>
                {r.email}
              </div>
            )}
          </div>
        </div>
      ),
    },
    {
      label: METRICS.submitted.name,
      define: metricDefine(
        METRICS.submitted,
        `${periodLabel(period)} 기준입니다.`,
        "막대는 이 표에서 가장 많이 제출한 사람 대비입니다.",
      ),
      kind: "number",
      align: "right",
      value: (r) => r.submitted,
      /*
        막대를 이 열 안에 둔다.
        사람이 많을 때 누가 얼마나 했는지 한눈에 보이고, 숫자 바로 아래에 있어 무엇의 막대인지 분명하다.
        막대만의 열을 따로 두면 1280px(지원 하한)에서 표가 넘친다.
      */
      cell: (r) => (
        <div className="flex flex-col items-end gap-1">
          <span className="font-semibold">{r.submitted.toLocaleString("ko-KR")}</span>
          <ThroughputBar value={r.submitted} max={maxSubmitted} />
        </div>
      ),
    },
    {
      label: METRICS.approvalRate.name,
      /*
        색을 칠하지 않는다. 사람 단위 수에는 판정 색을 쓰지 않는다.
        분모는 값 아래에 둔다.
      */
      define: metricDefine(
        METRICS.approvalRate,
        `${periodLabel(period)} 안의 판정만 셉니다.`,
        "값 아래 괄호는 승인 수와 판정 수입니다.",
      ),
      kind: "number",
      align: "right",
      // 값이 없으면 `null`이다. 대체값을 넣지 않는다. 정렬은 방향과 관계없이 `null`을 뒤로 보낸다.
      value: (r) => r.approval_rate,
      /*
        값, `측정 전`(제출은 있는데 판정 없음), `—`(제출도 판정도 없음) 중 무엇을 그릴지는 `lib/rates`가 고른다.
        괄호의 분모는 서버가 준 `approval_base`다. 화면이 `approved + rejected`를 다시 계산하면 분모 규칙이 두 곳에 생긴다.
        옆 `반려율`과 계산 대상이 다르다는 것도 이 괄호가 보여 준다.
      */
      cell: (r) => <RateCell view={approvalView(r)} />,
    },
    {
      /*
        반려율은 제출(작업량)이 분모라 승인율(판정 수가 분모)의 여집합과 다르다. 승인율은 검수에서 승인된 비율, 반려율은 제출량 대비 재작업 비율이다.
        해결 건 기준은 검수 속도에 따라 흔들리지만(50건 중 2건만 검수되고 1건이 반려되면 50%), 작업량 기준은 흔들리지 않는다.
        대신 검수 전 건도 계산에 들어가 초기에는 낮게 나오므로 값 아래에 `(반려/제출)`을 쓴다. `0% (0/5)`는 제출 5건 중 아직 반려가 없다는 뜻이다.
        이 수는 등급이 아니라서 색을 칠하지 않는다.
      */
      label: METRICS.rejectionRate.name,
      define: metricDefine(
        METRICS.rejectionRate,
        `${periodLabel(period)} 안의 제출과 반려를 셉니다.`,
        "값 아래 괄호는 반려 수와 제출 수이고, 판정이 제출보다 많으면 판정 수입니다.",
      ),
      kind: "number",
      align: "right",
      value: (r) => r.rejection_rate,
      /*
        괄호의 분모는 서버가 반려율을 낸 `rejection_base`다. 기간 전에 낸 작업이 기간 안에 판정되면 판정 수가 제출 수보다 커서
        서버는 판정 수로 나눈다. 괄호를 제출 수로 쓰면 `100% (18/0)`처럼 비율과 다른 수가 나온다. 제출도 판정도 없으면 `—`다.
        승인율과 같은 소수 1자리로 쓴다. 한 표의 두 비율이 반올림을 달리하면 `71.4%` 옆 `29%`처럼 정밀도가 다르게 읽힌다.
        사람 서랍도 같은 함수를 쓴다.
      */
      cell: (r) => <RateCell view={rejectionView(r)} />,
    },
    {
      /*
        수정률은 AI 초안 대비 교정한 글자의 비율이고 서버가 낸다(`_edit_rate`). 재제출은 직전 제출에서 시작하므로 작업마다 첫 제출만 잰다.
        이 열은 값, `측정 전`(제출은 있지만 잰 제출 없음), `—`(제출 없음) 중 하나를 고른다(`lib/edit_rate`). `측정 전`을 0%로 그리면 하나도 고치지 않은 것으로 읽힌다.
        정의상 100%를 넘을 수 있어 값을 자르지 않고 괄호에 글자 수의 분자·분모를 쓴다. 넘는 이유는 점선 밑줄 설명이 덧붙인다(`editRateNote`).
        고친 양은 초안 품질에 따라 달라 좋고 나쁨을 뜻하지 않으므로 색을 칠하지 않는다.
      */
      label: METRICS.editRate.name,
      define: editRateDefine(periodLabel(period)),
      kind: "number",
      align: "right",
      // 값이 없으면 `null`이다. 정렬은 방향과 관계없이 `null`을 뒤로 보낸다(승인율과 같은 규칙).
      value: (r) => r.edit_rate,
      cell: (r) => {
        const v = editRateView(r);
        if (v.kind !== "rate") return <span className="text-label-3">{v.text}</span>;
        return (
          <span className="inline-flex flex-col items-end gap-0.5 tabular-nums">
            <DefinedTerm define={editRateNote(r)}>{v.text}</DefinedTerm>
            <span className="text-caption text-label-3">{editRateFraction(r)}</span>
          </span>
        );
      },
    },
  ];

  return (
    <>
      {/* 사람별 표(카드형). 집계는 표 머리의 인원 수와 행으로 충분하다. */}
      <TableCard
        /* 제목에 무엇을 세는지 쓴다. 표는 사람을 싣고 라벨링을 세므로 제목이 `라벨러`면
           다른 역할의 `—` 행이 일하지 않은 것처럼 보인다. */
        title="사람별 라벨링"
        /* 받기 전에는 수를 그리지 않는다. 숨긴 사람이 있으면 그 수를 함께 밝힌다. */
        count={
          perf.data == null
            ? ""
            : `${shown.length.toLocaleString("ko-KR")}명${!showAll && others > 0 ? ` · ${others.toLocaleString("ko-KR")}명 숨김` : ""}`
        }
        action={
          others > 0 && (
            <Button size="sm" variant="secondary" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "라벨링하는 사람만" : "모두 보기"}
            </Button>
          )
        }
        columns={columns}
        rows={shown}
        unit="명"
        rowKey={(r) => r.user_id}
        loading={perf.isLoading}
        error={perf.isError}
        onRetry={() => perf.refetch()}
        /*
          기본 정렬은 제출 내림차순이다. 인덱스 1이 `제출` 열이다.
          서버 정렬(`submitted desc`)과 같다. 승인율 오름차순으로 열면 첫 줄이 승인율이 가장 낮은 사람이 되어
          "사람에게 먼저 빨간불을 켜지 않는다"는 원칙과 어긋난다.
        */
        initialSort={{ i: 1, dir: "desc" }}
        search="이름·이메일 검색"
        // 행을 누르면 그 사람의 상세 드로어를 연다. onRowClick은 행 안의 버튼·링크 클릭을 무시한다.
        onRowClick={setSelected}
        /*
          서버 행은 활성 멤버 전원과 기간 안에 제출한 멤버 아닌 사람이다.
          서버가 기간과 관계없이 멤버를 싣고 수만 기간으로 센다.
          표가 비는 경우는 둘이다. 라벨링하지 않는 멤버만 있어 모두 숨겼거나, 멤버가 없다.
          기간을 넓혀도 표가 채워지지 않으므로 기간을 넓히라는 안내를 쓰지 않는다.
        */
        empty={
          others > 0 ? (
            <EmptyState
              message="라벨링하는 사람 없음"
              description="라벨링하지 않는 멤버는 숨겼습니다."
              action={
                <Button size="sm" variant="secondary" onClick={() => setShowAll(true)}>
                  모두 보기
                </Button>
              }
            />
          ) : (
            <EmptyState message="활성 멤버 없음" />
          )
        }
      />

      {/* 행을 누르면 사람 서랍(실적 탭)을 연다. 멤버 화면과 같은 서랍이다. */}
      <PersonDrawer
        pid={project.project_id}
        userId={selected?.user_id ?? null}
        fallbackName={selected ? { name: selected.name, email: selected.email } : undefined}
        onClose={() => setSelected(null)}
        initialTab="perf"
      />
    </>
  );
}
