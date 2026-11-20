/**
 * 고정 상단바(콘텐츠 영역 상단, sticky). 모든 화면이 같은 상단바를 쓴다.
 *
 * 사이드바 접기와 라우트에서 만든 위치 표시(브레드크럼)를 둔다.
 * 세션은 작업하는 동안 `useSessionKeepalive`가 백그라운드에서 갱신하고, 만료되면 `_layout` 모달이 다시 로그인하게 한다.
 * 단축키 창은 재생 키가 있는 작업 화면이 각자 연다. 다크 모드 전환은 계정 메뉴에 있다. 페이지별 PageHeader는 제목·주요 액션만 맡는다.
 */
import { PanelLeft } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { Skeleton } from "@/shared/ui/Skeleton";
import { useDocumentTitleWriter, usePageTitle } from "@/shared/hooks/useDocumentTitle";
import { slugFromPath } from "@/hooks/useSlugProject";
import { cn } from "@/shared/lib/cn";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

import { resolveBreadcrumbs } from "./breadcrumbs";

const iconBtn =
  "inline-flex h-8 w-8 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

/** 상단바를 그린다. 사이드바 접기 버튼과 브레드크럼을 담는다. */
export function TopBar({
  onToggleSidebar,
  sidebarCollapsed = false,
}: {
  /** 사이드바 접기 토글. 없으면(좁은 폭에서 사이드바가 강제로 접힐 때) 버튼을 그리지 않는다. */
  onToggleSidebar?: () => void;
  /** 사이드바가 지금 접혀 있으면 true. 토글 버튼의 이름과 `aria-expanded`를 정한다. */
  sidebarCollapsed?: boolean;
}) {
  const location = useLocation();
  const { user } = useAuth();
  const { selectedProjectId } = useSelectedProject();

  const projects = useProjects({ enabled: !!user });
  // 주소에 프로젝트가 있으면 주소의 프로젝트를 쓴다. 고른 프로젝트는 주소로 맞추기 전 지난번 값일 수 있어서,
  // 고른 프로젝트를 쓰면 링크로 들어온 직후 이전 프로젝트 이름이나 slug가 뜬다.
  const urlSlug = slugFromPath(location.pathname);
  const project = urlSlug
    ? projects.data?.find((p) => p.slug === urlSlug)
    : projects.data?.find((p) => p.project_id === selectedProjectId);
  const pageTitle = usePageTitle();

  const crumbs = resolveBreadcrumbs(location.pathname, location.search, {
    projectName: projects.data ? (project?.name ?? null) : undefined,
    role: projects.data ? (project?.my_role ?? null) : undefined,
    globalRole: user?.global_role ?? null,
    pageTitle,
  });
  // 탭 제목은 이 컴포넌트 한 곳에서 쓴다. 화면이 올린 이름이 없으면 브레드크럼 끝 마디를 쓴다(`useDocumentTitle`).
  // 프로젝트 이름은 주소에 프로젝트가 있는 화면에서만 붙인다.
  useDocumentTitleWriter(
    crumbs[crumbs.length - 1]?.label || null,
    urlSlug ? (project?.name ?? null) : null,
  );

  return (
    <div
      /* 전체 화면 워크벤치가 열리면 이 셸 요소를 `inert`로 만든다. `WorkbenchHeader`가 이 속성으로 요소를 찾는다. */
      data-shell-chrome=""
      /* 높이 52px, 아래 선 하나. 사이드바 브랜드 줄과 높이가 같아 왼쪽 위 밑선이 맞는다. */
      className="sticky top-0 z-20 h-13 border-b border-separator bg-canvas"
    >
      <div className="flex h-full items-center gap-3 px-5">
        <div className="flex min-w-0 items-center gap-1.5">
          {/* 좁은 폭에서는 사이드바가 강제로 접혀 눌러도 반응이 없으므로 그리지 않는다.
              스크린리더가 `현재 위치` 안에서 버튼을 읽지 않도록 버튼은 브레드크럼 목록(`nav`) 밖에 둔다.
              이름은 누르면 일어나는 일로 쓴다(`사이드바 접기`·`사이드바 펼치기`). */}
          {onToggleSidebar && (
            <button
              type="button"
              onClick={onToggleSidebar}
              className={cn(iconBtn, "-ml-1.5 mr-0.5 shrink-0")}
              title={sidebarCollapsed ? "사이드바 펼치기" : "사이드바 접기"}
              aria-label={sidebarCollapsed ? "사이드바 펼치기" : "사이드바 접기"}
              aria-expanded={!sidebarCollapsed}
            >
              <PanelLeft size={16} aria-hidden="true" />
            </button>
          )}
          <nav className="min-w-0" aria-label="현재 위치">
            {/* 브레드크럼 항목은 모두 줄어들 수 있게 둔다. flex-shrink는 크기에 비례하므로 가장 긴 프로젝트명이 먼저 줄어든다.
                링크 항목만 `shrink-0`이면 폭이 모자랄 때 글자가 자기 래퍼를 넘어 다음 항목 위에 겹치고,
                래퍼끼리는 겹치지 않아 `nav`의 `scrollWidth`도 늘지 않아서 스크롤 없이 글자만 포개진다.
                순서가 있는 경로라 `ol`로 그리고, 스크린리더는 구분자 `/`를 읽지 않는다. */}
            <ol className="flex min-w-0 items-center gap-1.5 text-footnote">
              {crumbs.map((c, i) => (
                <li key={i} className="flex min-w-0 items-center gap-1.5">
                  {i > 0 && <span aria-hidden="true" className="shrink-0 text-label-4">/</span>}
                  {c.pending ? (
                    // 이름을 받기 전에는 slug나 이전 이름 대신 골격만 둔다.
                    <Skeleton className="h-3 w-24" />
                  ) : c.to ? (
                    // 부모 마디는 누르는 링크라 `text-label-2`로 두어 현재 마디 다음으로 읽히게 한다.
                    <Link
                      to={c.to}
                      className="min-w-0 truncate text-label-2 transition-colors hover:text-label"
                    >
                      {c.label}
                    </Link>
                  ) : i === crumbs.length - 1 ? (
                    <span className="min-w-0 truncate font-semibold text-label" aria-current="page">
                      {c.label}
                    </span>
                  ) : (
                    // 링크가 없는 부모 마디(`내 작업공간`). 굵게 쓰면 지금 위치가 두 개로 읽힌다.
                    <span className="min-w-0 truncate text-label-2">{c.label}</span>
                  )}
                </li>
              ))}
            </ol>
          </nav>
        </div>

      </div>
    </div>
  );
}
