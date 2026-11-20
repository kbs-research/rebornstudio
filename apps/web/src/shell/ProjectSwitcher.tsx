/**
 * 사이드바 상단의 프로젝트 전환기.
 *
 * 현재 프로젝트(아바타와 이름)를 보여 주고, 누르면 팝오버(검색, 프로젝트 목록, 역할,
 * 프로젝트 추가)를 연다. 프로젝트를 고르면 selectedProjectId를 바꾸고, 지금 보던 화면을 그
 * 프로젝트에서 연다(`switchTarget`).
 *
 * 고른 것이 없으면 첫 프로젝트를 임의로 고르는 대신 `프로젝트 선택`이라고 쓴다.
 * 참여 프로젝트가 하나뿐일 때만 그 하나를 고른다. 고를 것이 하나라 다른 해석이 없다.
 * 목록 조회가 실패하거나 0개여도 버튼은 남기고 팝오버 안에서 상태를 보여 준다.
 * 버튼이 사라지면 팝오버 안의 `프로젝트 조회 실패`·`참여 프로젝트 없음`을 볼 수 없다.
 *
 * 목록은 ↑↓로 움직이고 Enter로 고른다. 검색 입력란은 프로젝트가 `SEARCH_FROM`개를 넘을 때만 두고,
 * 이름과 주소를 같이 찾는다. 행은 머리글자 · 이름 · 역할이고, 지금 프로젝트는 오른쪽 체크다.
 * 바닥에 프로젝트 목록 링크와 키 안내(↑↓ 이동 · Enter 열기)를 둔다.
 * 프로젝트 선택이 필요한 빈 상태는 `requestProjectSwitcher`(`store/project`)로 이 팝오버를 연다.
 */
import { ArrowRight, Check, ChevronsUpDown, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { Avatar } from "@/shared/ui/Avatar";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { ProjectDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { projectRoleLabel } from "@/lib/role_label";
import { useAuth } from "@/store/auth";
import { useProjectSwitcherRequest, useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

import { PERSONAL_PATHS, SURFACES, surfaceVisible } from "./nav-config";

/**
 * 프로젝트를 바꾼 뒤 갈 곳을 정한다. `null`이면 지금 화면에 머문다.
 *
 * 개인 화면(`/my/*`)은 전환기 값을 따르는 화면이라 그대로 둔다.
 * 프로젝트 화면(`/p/{slug}/{표면}`)은 새 프로젝트의 같은 표면으로 간다. 그 역할에게
 * 보이지 않는 표면이거나 표면 아래의 하위 화면(영상 상세 등)이면 개요로 간다.
 * 그 밖의 화면(프로젝트 목록·플랫폼 화면)은 새 프로젝트의 개요로 간다.
 * 어디서 바꾸든 개요로 보내면, 내 작업에서 다른 프로젝트의 대기열을 보려던 라벨러가 화면을 떠나게 된다.
 */
export function switchTarget(pathname: string, p: ProjectDTO, isSuper: boolean): string | null {
  if (pathname.startsWith("/my/") || PERSONAL_PATHS.includes(pathname)) return null;
  const overview = `/p/${p.slug}/overview`;
  const m = /^\/p\/[^/]+\/([^/?#]+)\/?$/.exec(pathname);
  if (!m) return overview;
  const ctx = { slug: p.slug, role: p.my_role ?? null, isSuper };
  const surface = SURFACES.find((s) => s.to({ ...ctx, slug: "_" }) === `/p/_/${m[1]}`);
  if (!surface || !surfaceVisible(surface, ctx.role, isSuper)) return overview;
  return surface.to(ctx);
}

/**
 * 행 머리글자를 고른다. 이름의 첫 한글 글자, 없으면 첫 글자다(프로젝트 목록과 같은 규칙).
 * 첫 글자만 쓰면 `ABC …` 프로젝트가 모두 `A`가 되어 서로 구분되지 않는다.
 */
function initialOf(name: string): string {
  const m = /[가-힣]/.exec(name);
  return m ? m[0] : name.trim().charAt(0).toUpperCase() || "?";
}

/** 검색 입력란을 두는 프로젝트 수의 기준. 몇 개뿐이면 훑어보는 편이 빠르다(프로젝트 목록은 10). */
const SEARCH_FROM = 6;

/** 프로젝트 전환 버튼과 팝오버를 그린다. 목록 조회가 실패하거나 0개여도 버튼은 남긴다. */
export function ProjectSwitcher({ collapsed }: { collapsed: boolean }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { user } = useAuth();
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const { data, isError, isLoading, refetch } = useProjects({ enabled: !!user });

  // 화면이 보낸 열기 신호(`requestProjectSwitcher`). 처음 값(0)에서는 열지 않는다.
  const openRequest = useProjectSwitcherRequest((s) => s.nonce);
  useEffect(() => {
    if (openRequest > 0) setOpen(true);
  }, [openRequest]);

  // 저장된 선택이 목록에 없으면 비운다. 참여 프로젝트가 하나뿐일 때만 그 프로젝트를 고른다.
  useEffect(() => {
    if (!data) return;
    const valid = !!selectedProjectId && data.some((p) => p.project_id === selectedProjectId);
    if (valid) return;
    const next = data.length === 1 ? data[0].project_id : null;
    if (next !== selectedProjectId) setSelectedProjectId(next);
  }, [data, selectedProjectId, setSelectedProjectId]);

  /*
    바깥 클릭이나 Escape로 닫는다. 앱에서 열리는 다른 요소와 같은 동작이다.
    열면 포커스가 검색 입력란이나 목록으로 들어가므로, Escape로 닫을 때 전환 버튼으로 돌려준다.
    돌려주지 않으면 포커스가 창과 함께 사라져 `<body>`로 떨어진다.
  */
  useOutsideClose(open, ref, () => setOpen(false), { returnFocus: triggerRef });

  const current = data?.find((p) => p.project_id === selectedProjectId);
  const isSuper = user?.global_role === "super_admin";
  const canCreate = isSuper;
  const needle = q.trim().toLowerCase();
  const filtered = (data ?? []).filter(
    (p) => !needle || p.name.toLowerCase().includes(needle) || p.slug.includes(needle),
  );
  const searchable = (data?.length ?? 0) > SEARCH_FROM;

  /**
   * ↑↓로 목록의 버튼 사이를 옮긴다. 검색 입력란에서 ↓를 누르면 첫 항목으로 간다.
   * Enter는 포커스가 있는 버튼이 받으므로 따로 처리하지 않는다.
   */
  const moveFocus = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
    if (!buttons.length) return;
    e.preventDefault();
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next =
      at < 0
        ? e.key === "ArrowDown" ? 0 : buttons.length - 1
        : (at + (e.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length;
    buttons[next].focus();
  };

  // 모든 행의 역할이 같으면 역할을 줄마다 되풀이하지 않는다.
  const roles = new Set((data ?? []).map((p) => p.my_role ?? ""));
  const showRole = roles.size > 1;

  const pick = (p: ProjectDTO) => {
    setSelectedProjectId(p.project_id);
    setOpen(false);
    setQ("");
    const to = switchTarget(pathname, p, isSuper);
    if (to) navigate(to);
  };

  // 버튼 글자. 고른 프로젝트가 있으면 그 이름을, 없으면 지금 상태를 쓴다. 목록이 오기 전에는 빈 이름을 그리지 않는다.
  const buttonText = current?.name
    ?? (isLoading ? "" : isError ? "프로젝트 조회 실패" : data?.length === 0 ? "참여 프로젝트 없음" : "프로젝트 선택");
  if (!user) return null;

  return (
    <div ref={ref} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2 rounded-control text-left transition-colors duration-short focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
          // 펼친 사이드바에서는 선을 두른 상자 안에 `프로젝트` 이름표와 지금 프로젝트를 둔다.
          collapsed ? "justify-center px-0 py-1.5 hover:bg-fill" : "border border-separator bg-panel px-3 py-2.25 hover:bg-raised",
        )}
        title={current?.name}
        // 보이는 이름을 덮지 않아서 스크린리더가 버튼의 역할과 지금 값을 같이 읽는다.
        aria-label={`프로젝트 전환, 현재 ${current?.name ?? (buttonText || "불러오는 중")}`}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        {collapsed && <Avatar name={current?.name ?? "?"} size={22} />}
        {!collapsed && (
          <>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              {/* 흑연 면(`bg-panel`) 위 11px 이름표라 `text-label-2`로 읽기 쉽게 둔다. */}
              <span className="text-micro font-normal text-label-2">프로젝트</span>
              <span
                className={cn(
                  "truncate text-footnote font-semibold",
                  current ? "text-label" : "text-label-3",
                )}
              >
                {buttonText}
              </span>
            </span>
            <ChevronsUpDown size={14} className="shrink-0 text-label-3" aria-hidden="true" />
          </>
        )}
      </button>

      {open && (
        /* 폭은 사이드바 밖으로 넓힌다. 210px에서는 날짜·구분어가 붙은 이름이 대부분 잘려
           비슷한 프로젝트를 구분할 수 없다. */
        <div
          role="dialog"
          aria-label="프로젝트 전환"
          onKeyDown={moveFocus}
          className="absolute left-0 top-full z-50 mt-1 w-[19rem] max-w-[calc(100vw-1rem)] origin-top animate-pop-in overflow-hidden rounded-overlay border border-separator bg-raised shadow-pop"
        >
          {searchable && (
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="프로젝트 이름 또는 주소"
              aria-label="프로젝트 이름 또는 주소로 검색"
              className="mx-1.5 mt-1.5 w-[calc(100%-0.75rem)] rounded-control border border-separator-strong bg-panel px-2.5 py-1.5 text-footnote text-label placeholder:text-placeholder focus:border-focus focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/25"
            />
          )}
          <ul ref={listRef} className="max-h-80 overflow-y-auto p-1.5">
            {filtered.map((p, i) => {
              const selected = p.project_id === selectedProjectId;
              return (
                <li key={p.project_id}>
                  <button
                    type="button"
                    // 검색 입력란이 없으면 첫 항목에 포커스를 두어 열자마자 ↑↓로 움직이게 한다.
                    autoFocus={!searchable && (selected || (!selectedProjectId && i === 0))}
                    onClick={() => pick(p)}
                    aria-current={selected ? "true" : undefined}
                    title={p.name}
                    className="flex w-full items-center gap-2.5 rounded-control px-2 py-2 text-left transition-colors hover:bg-canvas-2 focus-visible:bg-canvas-2 focus-visible:outline-none"
                  >
                    <span
                      aria-hidden="true"
                      className="flex size-7 shrink-0 items-center justify-center rounded-mark border border-separator bg-panel text-caption font-semibold text-label"
                    >
                      {initialOf(p.name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-footnote font-semibold text-label">{p.name}</span>
                      {/* 역할 이름은 기준 목록(`lib/role_label`)을 그대로 쓴다. 축약 사본을 두지 않는다. */}
                      {showRole && p.my_role && (
                        <span className="block truncate text-caption text-label-2">
                          {projectRoleLabel(p.my_role)}
                        </span>
                      )}
                    </span>
                    {selected && <Check size={14} className="shrink-0 text-label" aria-hidden="true" />}
                  </button>
                </li>
              );
            })}
          </ul>
          {/*
            드롭다운의 결과 없음 표시는 `ui/FilterMenu`와 같은 `EmptyState dense`로 그리고,
            조회 실패·불러오는 중·검색 결과 없음·참여 프로젝트 없음을 나눈다.
            목록을 `data ?? []`로 받으면 조회가 실패해도 `filtered`가 0이 되어, 프로젝트가 없는 사람과 같은 "결과 없음"이 보인다.
            검색 중이 아닐 때 "검색어와 일치하는"이라고 쓰면 입력하지도 않은 검색을 원인으로 가리키게 된다.
          */}
          {filtered.length === 0 &&
            (isError ? (
              <div className="px-2 py-2">
                <ErrorState inline message="프로젝트 조회 실패" onRetry={() => void refetch()} />
              </div>
            ) : isLoading ? (
              <div role="status" aria-label="프로젝트 불러오는 중" className="space-y-2 px-2 py-2">
                {["w-3/5", "w-2/3", "w-1/2"].map((w) => (
                  <div key={w} className="flex items-center gap-2">
                    <Skeleton className="size-5 shrink-0 rounded-control" />
                    <Skeleton className={cn("h-3", w)} />
                  </div>
                ))}
              </div>
            ) : q.trim() ? (
              <EmptyState dense message="검색 결과 없음" />
            ) : (
              /* 같은 상황에 두 가지 안내가 나오지 않도록 문구는 프로젝트 목록 화면(`features/projects/pages/projects.tsx`)과 같다.
                 초대 요청 안내는 프로젝트를 만들 수 없는 사람에게만 붙인다. */
              <EmptyState
                dense
                message="참여 프로젝트 없음"
                description={canCreate ? undefined : "관리자에게 프로젝트 초대를 요청합니다."}
              />
            ))}
          {/* 바닥 줄. 상태는 목록 화면에서 본다. 전환기는 고르는 일만 한다. */}
          <div className="flex items-center justify-between gap-2 border-t border-separator px-3.5 py-2.5">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/projects");
              }}
              className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-control text-footnote font-semibold text-label hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              프로젝트 목록
              <ArrowRight size={14} aria-hidden="true" />
            </button>
            <span aria-hidden="true" className="flex shrink-0 items-center gap-1 whitespace-nowrap text-caption text-label-2">
              <kbd className="rounded-mark border border-separator-strong px-1 font-mono">↑</kbd>
              <kbd className="rounded-mark border border-separator-strong px-1 font-mono">↓</kbd>
              이동
              <kbd className="ml-1 rounded-mark border border-separator-strong px-1 font-mono">Enter</kbd>
              열기
            </span>
          </div>
          {canCreate && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/projects/new");
              }}
              className="flex w-full items-center gap-2 border-t border-separator px-3.5 py-2.5 text-left text-footnote font-semibold text-label transition-colors hover:bg-canvas-2"
            >
              <Plus size={14} className="shrink-0" aria-hidden="true" /> 프로젝트 추가
            </button>
          )}
        </div>
      )}
    </div>
  );
}
