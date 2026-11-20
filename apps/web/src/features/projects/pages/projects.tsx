/**
 * 프로젝트 목록 화면. 앱의 진입점이다.
 *
 * 보는 사람에 따라 모양이 둘이다.
 * 작업자(참여한 프로젝트만 보는 사람)에게는 한 장 안의 행 목록을 보여 준다.
 * 행은 머리글자, 이름과 설명, 역할, 상태를 쓴다. 전환기에서 고른 프로젝트에
 * `지금 보는 프로젝트`를 달아 개인 작업공간이 어느 프로젝트 기준인지 이 화면에서도 알 수 있다.
 * 프로젝트를 만드는 사람(최고관리자)에게는 표를 보여 준다.
 * 분석 방식·계약 목표·마감·상태·만든 날을 나란히 비교한다. 값은 모두 `ProjectDTO`에 있다.
 *
 * 전 행이 같은 값인 열은 그리지 않는다. 역할은 행마다 다를 때만 쓴다.
 * 상태는 활성이 아닌 프로젝트가 있을 때만 열을 둔다.
 * 목록이 길어지면(`SEARCH_FROM` 초과) 작업자 목록에도 검색과 상태 거르기를 붙인다.
 */
import { type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Check, ChevronRight, Plus, Search } from "lucide-react";

import { buttonClasses } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip, type ChipTone } from "@/shared/ui/Chip";
import { DataTable, useTableUrlState, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { useDocumentTitle } from "@/shared/hooks/useDocumentTitle";
import type { ProjectDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { projectRoleLabel } from "@/lib/role_label";
import { fmtDate } from "@/shared/lib/time";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";

import { useProjects } from "../api";
import { PROJECT_STATUS_LABEL, type ProjectStatus } from "../lib/project_status";

/** 프로젝트가 이 수를 넘으면 작업자 목록에 검색과 상태 필터를 붙인다. 몇 개뿐이면 훑어보는 편이 더 빠르다. */
const SEARCH_FROM = 10;

/** 프로젝트 상태 점의 톤. 이름은 `lib/project_status`에 있다. */
const STATUS_TONE: Record<ProjectStatus, ChipTone> = {
  active: "ok",
  paused: "warn",
  completed: "neutral",
};

/**
 * 행 머리글자를 고른다. 이름의 첫 한글 글자, 없으면 첫 글자다.
 * 첫 글자만 쓰면 `ABC …` 프로젝트가 모두 `A`가 되어 서로 구분되지 않는다.
 */
function initialOf(name: string): string {
  const m = /[가-힣]/.exec(name);
  return m ? m[0] : name.trim().charAt(0).toUpperCase() || "?";
}

/** 제목과 수 칩을 그린다. 제목을 탭 제목으로도 올린다. */
function ListHeader({ title, count, action }: { title: string; count?: string; action?: ReactNode }) {
  useDocumentTitle(title);
  return (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2.5">
        <h1 className="text-title-1 text-label">{title}</h1>
        {count && <Chip>{count}</Chip>}
      </div>
      {action}
    </header>
  );
}

/**
 * 운영하는 사람의 프로젝트 표 열. 비교할 값을 열로 둔다.
 * 처음 정렬은 만든 날(마지막 열) 최신순이다. 정렬 상태는 고정 문구 대신 열 머리의 캐럿으로 표시한다.
 */
const ADMIN_COLUMNS: DataColumn<ProjectDTO>[] = [
  {
    label: "프로젝트",
    kind: "title",
    value: (p) => p.name,
    cell: (p) => (
      <span className="block min-w-0">
        <span className="block truncate font-semibold text-label">{p.name}</span>
        <span className="block truncate font-mono text-caption text-label-2">/p/{p.slug}</span>
      </span>
    ),
  },
  { label: "분석 방식", width: "13rem", value: (p) => p.analysis_preset_label ?? null, cell: (p) => p.analysis_preset_label ?? "—" },
  {
    label: "계약 목표",
    align: "right",
    width: "7rem",
    value: (p) => p.target_hours ?? null,
    cell: (p) => (p.target_hours != null ? `${p.target_hours.toLocaleString("ko-KR")}시간` : "—"),
  },
  {
    label: "마감",
    align: "right",
    width: "9rem",
    value: (p) => p.contract_deadline ?? null,
    cell: (p) => (p.contract_deadline ? fmtDate(p.contract_deadline) : "—"),
  },
  {
    label: "상태",
    width: "7rem",
    value: (p) => PROJECT_STATUS_LABEL[p.status],
    cell: (p) => <Chip variant="dot" tone={STATUS_TONE[p.status]}>{PROJECT_STATUS_LABEL[p.status]}</Chip>,
  },
  {
    label: "만든 날",
    align: "right",
    width: "9rem",
    value: (p) => p.created_at ?? null,
    cell: (p) => (p.created_at ? fmtDate(p.created_at) : "—"),
  },
];

/** 현재 사용자가 참여한 프로젝트 목록을 보여 주고, 고른 프로젝트 홈으로 이동시킨다. */
export function ProjectsPage() {
  const navigate = useNavigate();
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();
  const user = useAuth((s) => s.user);
  const isSuper = user?.global_role === "super_admin";
  const canCreate = isSuper;

  const { data, isLoading, error, refetch } = useProjects();
  /*
    검색어는 주소(`?q=`)에 둔다. 새로고침하거나 링크를 받은 사람이 같은 목록을 본다.
    표의 정렬·페이지도 같은 주소에 둔다(`useTableUrlState`).
  */
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v);
    else next.delete(k);
    next.delete("t.page");
    setSp(next, { replace: true });
  };
  const setQ = (v: string) => setParam("q", v);
  // 검색어와 표 페이지를 한 번에 지운다. `setParam`을 두 번 부르면 두 번째 호출이 첫 번째 결과를 덮어쓴다.
  const clearSearch = () => {
    const next = new URLSearchParams(sp);
    ["q", "t.page"].forEach((k) => next.delete(k));
    setSp(next, { replace: true });
  };
  // 표 상태는 `t.` 접두어로 둔다. 표 밖 검색어(`q`)와 키가 겹치면 표가 그 키를 지운다.
  const tableUrl = useTableUrlState("t");

  function enter(p: ProjectDTO) {
    setSelectedProjectId(p.project_id);
    navigate(`/p/${p.slug}`);
  }

  if (isLoading) return <PageSkeleton cards={6} />;
  // 첫 화면이라 이 화면에서 막히면 갈 곳이 없다. 다시 시도가 있는 `ErrorState`로 그린다.
  if (error)
    return <ErrorState message="프로젝트 목록 조회 실패" description="잠시 후 다시 시도해주세요." onRetry={() => void refetch()} />;

  const all = data ?? [];
  const title = isSuper ? "전체 프로젝트" : "내 프로젝트";
  const addButton = canCreate ? (
    <Link to="/projects/new" className={buttonClasses("primary", "md")}>
      <Plus size={16} aria-hidden="true" />
      프로젝트 추가
    </Link>
  ) : undefined;
  const needle = q.trim().toLowerCase();
  const rows = all.filter(
    (p) => !needle || p.name.toLowerCase().includes(needle) || p.slug.includes(needle),
  );
  const filtered = rows.length !== all.length;
  // 검색으로 거른 목록이면 전체 수를 함께 쓴다. `3개`만 쓰면 전체가 3개인 것으로 읽힌다.
  const count = filtered ? `${all.length}개 중 ${rows.length}개` : `${all.length}개`;

  if (all.length === 0) {
    return (
      <div>
        {/* 추가 버튼은 빈 상태 안 하나만 둔다. 머리에도 두면 같은 버튼이 한 화면에 둘이 된다. */}
        <ListHeader title={title} />
        {/* 만들 수 있는 사람에게 "초대를 요청하세요"는 맞지 않는 안내다. */}
        <EmptyState
          message="참여 프로젝트 없음"
          description={canCreate ? undefined : "관리자에게 프로젝트 초대를 요청합니다."}
          action={addButton}
        />
      </div>
    );
  }

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative">
        <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-label-3" />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="프로젝트 이름 또는 주소"
          aria-label="프로젝트 이름 또는 주소 검색"
          className="h-8 w-72 max-w-full rounded-control border border-separator-strong bg-panel pl-8 pr-2.5 text-footnote text-label outline-none placeholder:text-placeholder focus:border-focus focus:ring-2 focus:ring-focus/25"
        />
      </label>
    </div>
  );

  // 프로젝트를 만드는 사람: 비교 표
  if (canCreate) {
    // 상태 열은 활성이 아닌 프로젝트가 있을 때만 둔다. 모두 활성이면 전 행이 `활성`이다.
    const columns = all.some((p) => p.status !== "active")
      ? ADMIN_COLUMNS
      : ADMIN_COLUMNS.filter((c) => c.label !== "상태");
    return (
      <div>
        <ListHeader title={title} count={count} action={addButton} />
        <Card as="section" aria-label={title} className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-separator px-4 py-3">
            {toolbar}
          </div>
          {/* 검색·상태는 표 밖에서 걸러 넘긴다. 분모 줄이 전체 수를 분모로 쓰도록 `total`을 준다. */}
          <DataTable<ProjectDTO>
            bare
            unit="개"
            columns={columns}
            rows={rows}
            total={all.length}
            initialSort={{ i: columns.length - 1, dir: "desc" }}
            urlState={tableUrl}
            rowKey={(p) => p.project_id}
            rowLabel={(p) => p.name}
            onRowClick={enter}
            empty={<EmptyState message="검색 결과 없음" />}
          />
        </Card>
      </div>
    );
  }

  // 작업자: 행 목록
  // 역할 열은 행마다 다를 때만 둔다. 모두 라벨러면 전 행이 `라벨러`가 된다.
  const showRole = new Set(all.map((p) => p.my_role ?? "")).size > 1;
  // 상태 열은 활성이 아닌 프로젝트가 있을 때만 둔다. 모두 활성이면 빈 열만 남는다.
  const showStatus = all.some((p) => p.status !== "active");
  const cols = [
    "2.5rem",
    "minmax(0,1fr)",
    showRole && "8rem",
    showStatus && "5rem",
    "1rem",
  ].filter(Boolean);
  const searchable = all.length > SEARCH_FROM;
  return (
    <div>
      <ListHeader title={title} count={count} />
      {searchable && <div className="mb-3">{toolbar}</div>}
      {rows.length === 0 ? (
        <EmptyState
          framed
          message="검색 결과 없음"
          action={
            <button
              type="button"
              onClick={clearSearch}
              className={buttonClasses("secondary", "sm")}
            >
              검색어 지우기
            </button>
          }
        />
      ) : (
        <Card as="section" aria-label={title} className="overflow-hidden">
          <ul className="divide-y divide-separator">
            {rows.map((p) => {
              const current = p.project_id === selectedProjectId;
              return (
                <li key={p.project_id}>
                  <button
                    type="button"
                    onClick={() => enter(p)}
                    aria-label={`${p.name} 프로젝트 홈으로 이동`}
                    // 열 수가 데이터에 따라 달라 열 틀을 인라인으로 준다. 유틸 클래스를 조합하면 빌드 CSS에 없는 이름이 된다.
                    style={{ gridTemplateColumns: cols.join(" ") }}
                    className={cn(
                      "grid w-full items-center gap-5 px-5 py-4 text-left transition-colors hover:bg-canvas-2",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className="flex size-10 items-center justify-center rounded-control bg-canvas-2 text-callout font-semibold text-label"
                    >
                      {initialOf(p.name)}
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-callout font-semibold text-label">{p.name}</span>
                        {current && (
                          <span className="inline-flex shrink-0 items-center gap-1 text-caption font-semibold text-label">
                            <Check size={12} aria-hidden="true" />
                            지금 보는 프로젝트
                          </span>
                        )}
                      </span>
                      {p.description && <span className="mt-0.5 block truncate text-footnote text-label-2">{p.description}</span>}
                    </span>
                    {showRole && (
                      <span>
                        {/* 역할 칩은 중립 한 톤이다. 의미 색은 상태에만 쓴다. */}
                        {p.my_role && <Chip>{projectRoleLabel(p.my_role)}</Chip>}
                      </span>
                    )}
                    {showStatus && (
                      <span className="justify-self-end">
                        {/* 상태는 `활성`이 아닐 때만 보인다. */}
                        {p.status !== "active" && (
                          <Chip variant="dot" tone={STATUS_TONE[p.status]}>{PROJECT_STATUS_LABEL[p.status]}</Chip>
                        )}
                      </span>
                    )}
                    <ChevronRight size={16} aria-hidden="true" className="text-label-3" />
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}
