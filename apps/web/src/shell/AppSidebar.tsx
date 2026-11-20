/**
 * 역할에 맞춰 항목을 보여 주는 셸 사이드바.
 *
 * 구조(위에서 아래로): 브랜드 링크, 프로젝트 전환기, 개인 작업공간 링크 1개,
 * 프로젝트 표면(역할별 1~9개), 플랫폼 관리 링크(super), 하단 계정 메뉴(내 계정, 다크 모드, 플랫폼 관리(super), 로그아웃).
 * 최고관리자가 플랫폼 화면에 있으면 전환기와 프로젝트 표면 대신 플랫폼 메뉴를 그린다.
 * 첫 설치에서 자격증명 연결과 사용자를 먼저 등록해야 하므로, 최고관리자에게는 계정 메뉴 밖에도 플랫폼 관리 링크를 둔다.
 *
 * 사이드바는 테마와 관계없이 흑연색이다(`data-surface="graphite"`). 안의 요소는 일반 유틸(`text-label`)을 쓰고
 * 흑연 값은 범위 선택자가 준다. 현재 위치는 호박색 점 하나와 흑연 750 면으로 표시한다.
 */
import { LogOut, Moon, Shield, UserCog, UserSquare2 } from "lucide-react";
import { type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { Avatar } from "@/shared/ui/Avatar";
import { EditionLabel } from "@/shared/ui/Wordmark";
import { PRODUCT_FULL_NAME, PRODUCT_NAME } from "@/shared/lib/edition";
import { slugFromPath } from "@/hooks/useSlugProject";
import { PROJECT_ROLE_LABEL as ROLE_LABEL } from "@/lib/role_label";
import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { cn } from "@/shared/lib/cn";
import type { ProjectRole } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useTheme } from "@/store/theme";
import { useProjects } from "@/features/projects";

import { ProjectSwitcher } from "./ProjectSwitcher";
import {
  isPlatformPath,
  PERSONAL_PATHS,
  PLATFORM_ITEMS,
  platformItemActive,
  SURFACES,
  surfaceLink,
  surfaceVisible,
} from "./nav-config";

/**
 * 사이드바 항목의 클래스를 만든다.
 *
 * 펼친 상태는 글자만 둔다. 현재 위치는 앞의 6px 호박색 점과 면으로 표시한다.
 * 접힌 상태는 아이콘을 위에, 짧은 이름과 수를 아래에 둔다. 수를 아이콘 모서리에 올리면 73.5px 폭에서 아이콘을 덮는다.
 */
function navCls(active: boolean, collapsed: boolean) {
  return cn(
    "relative flex w-full rounded-control transition-colors duration-short focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
    active ? "bg-raised font-semibold text-label" : "text-label/80 hover:bg-fill hover:text-label",
    collapsed
      ? "flex-col items-center gap-1 px-1 py-2 text-nano"
      : "items-center gap-2 px-3 py-2 text-footnote",
  );
}

interface Props {
  collapsed: boolean;
}

/** 역할에 맞춘 셸 사이드바를 그린다. */
export function AppSidebar({ collapsed }: Props) {
  const location = useLocation();
  const { user } = useAuth();
  const { selectedProjectId } = useSelectedProject();

  const projects = useProjects({ enabled: !!user });
  // 주소에 프로젝트가 있으면 주소의 프로젝트 메뉴를 그린다. 고른 프로젝트는 주소로 맞추기 전 지난번 값일 수 있다.
  // 목록에 없는 slug면 고른 프로젝트를 쓴다(본문은 셸이 `프로젝트 없음`으로 막는다).
  const urlSlug = slugFromPath(location.pathname);
  const project =
    (urlSlug ? projects.data?.find((p) => p.slug === urlSlug) : undefined)
    ?? projects.data?.find((p) => p.project_id === selectedProjectId);
  const slug = project?.slug;
  const myRole = (project?.my_role ?? null) as ProjectRole | null;
  const isSuper = user?.global_role === "super_admin";

  /* 단계마다 세는 단위(영상·클립·작업)가 달라 합계가 의미 없으므로 작업 단계 항목에는 배지를 달지 않는다.
     단계별 수는 파이프라인 스텝퍼가 보여 준다(`features/pipeline/lib/pipeline_counts`). */
  const surfaces = slug ? SURFACES.filter((s) => surfaceVisible(s, myRole, isSuper)) : [];
  // 최고관리자는 늘 `최고관리자`로 쓴다. 고른 프로젝트의 역할을 먼저 쓰면 목록이 오는 순간 이름이 바뀐다.
  const displayRole = isSuper ? ROLE_LABEL.super_admin : myRole ? ROLE_LABEL[myRole] : null;
  // 최고관리자가 플랫폼 화면에 있으면 사이드바 본문을 플랫폼 메뉴로 바꾼다.
  const onPlatform = isSuper && isPlatformPath(location.pathname);

  /** 항목이 활성이면 true를 돌려준다. 쿼리(예: ?stage=review)가 있으면 그 파라미터까지 일치해야 활성이다. */
  const isActive = (to: string) => {
    const [path, query] = to.split("?");
    const pathHit = location.pathname === path || location.pathname.startsWith(path + "/");
    if (!pathHit) return false;
    if (!query) return true;
    const want = new URLSearchParams(query);
    const have = new URLSearchParams(location.search);
    for (const [k, v] of want) if (have.get(k) !== v) return false;
    return true;
  };

  // `내 작업공간`의 목적지는 개인 표면의 첫 경로다.
  const personalTo = PERSONAL_PATHS[0];
  /* 항목이 일곱 개뿐이고 의미로 나뉘는 경계가 없어서 그룹 머리글 없이 한 목록으로 둔다. */
  const links = surfaces.map((s) => ({
    id: s.id,
    icon: s.icon,
    ...surfaceLink(s, { slug: slug ?? "", role: myRole, isSuper }),
  }));

  return (
    <aside
      /* 전체 화면 워크벤치가 열리면 이 셸 요소를 `inert`로 만든다. `WorkbenchHeader`가 이 속성으로 요소를 찾는다. */
      data-shell-chrome=""
      data-surface="graphite"
      className={cn(
        "fixed inset-y-0 left-0 z-sidebar flex flex-col border-r border-separator bg-sidebar text-label transition-all duration-normal",
        collapsed ? "w-[5.25rem]" : "w-58",
      )}
    >
      {/* 브랜드. 머리글(TopBar)과 높이를 같게 두어 왼쪽 위에서 로고와 브레드크럼의 밑선을 맞춘다. */}
      <Link
        to="/"
        className={cn(
          "flex h-14 shrink-0 items-center gap-2.5 px-5 text-body font-bold text-label",
          collapsed && "justify-center px-0",
        )}
        title={PRODUCT_FULL_NAME}
        aria-label={`${PRODUCT_FULL_NAME} 첫 화면`}
      >
        <span className="grid size-6 shrink-0 place-items-center rounded-control bg-amber text-footnote font-bold text-on-amber">
          R
        </span>
        {/* 에디션 이름은 제품 이름 옆에 작게 둔다. 접힌 레일에는 표식만 둔다. */}
        {!collapsed && (
          <>
            {PRODUCT_NAME}
            <EditionLabel />
          </>
        )}
      </Link>

      {/* 프로젝트 전환기와 개인 작업공간(스크롤하지 않는 상단). 플랫폼 화면에는 전환기를 두지 않는다. */}
      <div className={cn("shrink-0 space-y-3 pt-1", collapsed ? "px-1.5" : "px-3")}>
        {!onPlatform && <ProjectSwitcher collapsed={collapsed} />}
        {/* 개인 표면은 한 항목이다. 두 화면 사이 이동은 `PersonalTabs`가 맡는다. */}
        <SideLink
          to={personalTo}
          icon={<UserSquare2 size={16} className="shrink-0" />}
          label="내 작업공간"
          short="내 작업"
          active={PERSONAL_PATHS.includes(location.pathname)}
          collapsed={collapsed}
        />
      </div>

      {/* 프로젝트 표면(스크롤). 개인 항목과 종류가 달라 사이에 선을 긋고, 항목이 없으면(프로젝트 미선택) 선도 긋지 않는다.
          좌우 여백은 위 개인 항목과 같다. 접힌 레일에서 항목 폭이 같아야 현재 위치 면의 폭이 줄마다 같고
          다섯 자 이름(`감사 로그`)도 잘리지 않는다. */}
      <nav
        aria-label={onPlatform ? "플랫폼 메뉴" : "프로젝트 메뉴"}
        className={cn(
          "mt-2 flex-1 space-y-0.5 overflow-y-auto pt-2",
          collapsed ? "mx-1.5" : "mx-3",
          (onPlatform || links.length > 0) && "border-t border-raised",
        )}
      >
        {onPlatform
          ? PLATFORM_ITEMS.map((it) => (
              <SideLink
                key={it.id}
                to={it.to}
                icon={<it.icon size={16} className="shrink-0" />}
                label={it.label}
                short={it.short}
                active={platformItemActive(it.to, location.pathname)}
                collapsed={collapsed}
              />
            ))
          : links.map((l) => (
              <SideLink
                key={l.id}
                to={l.to}
                icon={<l.icon size={16} className="shrink-0" />}
                label={l.label}
                short={l.short}
                active={isActive(l.to)}
                collapsed={collapsed}
              />
            ))}
      </nav>

      {/* 플랫폼 관리(최고관리자). 프로젝트 표면과 종류가 달라 선으로 나눈다. 플랫폼 화면에서는 위 메뉴가 같은 곳을 가리키므로 두지 않는다. */}
      {isSuper && !onPlatform && (
        <div className={cn("shrink-0 border-t border-raised py-2", collapsed ? "mx-1.5" : "mx-3")}>
          <SideLink
            to={PLATFORM_ITEMS[0].to}
            icon={<Shield size={16} className="shrink-0" />}
            label="플랫폼 관리"
            short="플랫폼"
            active={false}
            collapsed={collapsed}
          />
        </div>
      )}

      {/* 하단 계정 영역은 메뉴다. 자주 쓰지 않는 항목은 한 번 눌러 연다. */}
      <div className={cn("shrink-0 border-t border-raised py-2", collapsed ? "mx-1.5" : "mx-3")}>
        {user && <AccountMenu user={user} role={displayRole} isSuper={isSuper} collapsed={collapsed} />}
      </div>
    </aside>
  );
}

/**
 * 계정 메뉴를 그린다. 아바타를 눌러 연다. 로그인 계정, 내 계정, 다크 모드 스위치, 플랫폼 관리(최고관리자), 로그아웃을 담는다.
 *
 * 다크 모드는 앱 전역 테마다. 워크벤치는 테마와 관계없이 흑연색이라 이 스위치를 따르지 않는다.
 * 플랫폼 화면은 `플랫폼 관리` 한 줄로 들어간다. 플랫폼 화면 사이 이동은 사이드바가 맡는다.
 */
function AccountMenu({
  user,
  role,
  isSuper,
  collapsed,
}: {
  user: { display_name?: string | null; email: string };
  role: string | null;
  isSuper: boolean;
  collapsed: boolean;
}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { signOut } = useAuth();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  /*
    Esc로 닫고 포커스를 계정 버튼으로 돌린다. 메뉴(role="menu")를 여는 버튼의 기본 동작이다.
    포커스는 닫는 처리 안에서 돌려준다. 따로 단 리스너는 앞 리스너의 닫기로 효과가 정리되어 실행되지 않는다.
  */
  useOutsideClose(open, ref, () => setOpen(false), { returnFocus: triggerRef });
  // 열면 첫 항목에 포커스를 두어 키보드로 연 사람이 곧바로 ↑↓로 고르게 한다.
  useEffect(() => {
    if (open) menuRef.current?.querySelector<HTMLButtonElement>("[role^=menuitem]")?.focus();
  }, [open]);

  /** ↑↓·Home·End로 메뉴 항목 사이를 옮긴다. 끝에서 반대쪽 끝으로 돈다. */
  const moveFocus = (e: ReactKeyboardEvent) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role^=menuitem]") ?? []);
    if (!items.length) return;
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    const next =
      e.key === "ArrowDown" ? (at + 1) % items.length
        : e.key === "ArrowUp" ? (at - 1 + items.length) % items.length
          : e.key === "Home" ? 0
            : e.key === "End" ? items.length - 1
              : -1;
    if (next < 0) return;
    e.preventDefault();
    items[next].focus();
  };

  const name = user.display_name ?? user.email;
  const dark = theme === "dark";
  const go = (to: string) => { setOpen(false); navigate(to); };
  const item =
    "flex h-9 w-full items-center gap-2.5 rounded-control px-2.5 text-left text-footnote " +
    "text-label transition-colors hover:bg-raised focus-visible:bg-raised focus-visible:outline-none " +
    "focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus";

  return (
    <div ref={ref} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          // 높이를 고정해 접힘(아바타만)과 펼침(이름·역할 2줄) 사이에서 하단 영역이 흔들리지 않게 한다.
          "flex h-11.5 w-full min-w-0 items-center gap-2.5 rounded-control px-2 text-left transition-colors hover:bg-fill",
          collapsed && "justify-center px-0",
          open && "bg-fill ring-1 ring-inset ring-separator-strong",
        )}
        title={`${name} · 계정 메뉴`}
      >
        <Avatar name={name} size={28} className="bg-raised" />
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-footnote font-semibold text-label">{name}</span>
            {role && <span className="block truncate text-micro font-normal text-label-3">{role}</span>}
          </span>
        )}
      </button>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="계정 메뉴"
          onKeyDown={moveFocus}
          /* 트리거가 사이드바 맨 아래에 있어 아래에 공간이 없으므로 메뉴를 위로 연다.
             펼친 사이드바에서는 트리거와 같은 폭이라 사이드바 경계 안에 있고, 접힌 레일은 좁아서 고정 폭으로 편다. */
          className={cn(
            "absolute bottom-full left-0 z-30 mb-1.5 origin-bottom-left animate-pop-in rounded-card border border-separator bg-canvas-2 p-1.5 shadow-e3",
            collapsed ? "w-60" : "right-0",
          )}
        >
          <div className="mb-1.5 flex flex-col gap-0.5 border-b border-raised px-2.5 pb-3 pt-2.5">
            <span className="text-caption text-label-3">로그인 계정</span>
            <span className="truncate font-mono text-caption text-label">{user.email}</span>
          </div>
          <button type="button" role="menuitem" className={item} onClick={() => go("/account")}>
            <UserCog size={14} className="shrink-0 text-label-3" aria-hidden="true" />
            <span className="flex-1">내 계정</span>
          </button>
          {/* 다크 모드. 누르면 메뉴를 연 채로 바로 바뀐다. 상태는 스위치 모양과 `aria-checked`로 알린다. */}
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={dark}
            className={item}
            onClick={toggle}
          >
            <Moon size={14} className="shrink-0 text-label-3" aria-hidden="true" />
            <span className="flex-1">다크 모드</span>
            <span
              aria-hidden="true"
              className={cn(
                "inline-flex h-5 w-8 shrink-0 items-center rounded-capsule p-0.5 transition-colors",
                dark ? "bg-accent" : "bg-separator-strong",
              )}
            >
              <span
                className={cn(
                  "size-4 rounded-capsule transition-transform",
                  dark ? "translate-x-3 bg-accent-fg" : "translate-x-0 bg-knob",
                )}
              />
            </span>
          </button>
          {isSuper && (
            <button
              type="button"
              role="menuitem"
              aria-current={platformItemActive("/admin", pathname) ? "page" : undefined}
              className={item}
              onClick={() => go("/admin")}
            >
              <Shield size={14} className="shrink-0 text-label-3" aria-hidden="true" />
              <span className="flex-1">플랫폼 관리</span>
            </button>
          )}
          <div className="mx-1 my-1.5 h-px bg-raised" />
          <button
            type="button"
            role="menuitem"
            className={cn(item, "text-bad-text hover:bg-bad-muted")}
            // 로그아웃은 방문 기록에 남기지 않는다(replace). 남기면 뒤로 가기가 로그아웃 직전 화면을 부르고,
            // 인증 가드가 곧바로 `/login`으로 되돌려 뒤로 가기가 동작하지 않는 것처럼 보인다.
            onClick={() => { signOut(); navigate("/login", { replace: true }); }}
          >
            <LogOut size={14} className="shrink-0" aria-hidden="true" />
            <span className="flex-1">로그아웃</span>
          </button>
        </div>
      )}
    </div>
  );
}

/** 사이드바 링크 한 줄을 그린다. 접힌 상태에서는 아이콘, 이름, 수를 세 줄로 가운데 정렬한다. */
function SideLink({
  to,
  icon,
  label,
  short,
  active,
  collapsed,
  badge,
  badgeLabel,
  badgeUnit = "건",
}: {
  to: string;
  icon: ReactNode;
  label: string;
  /** 접힘 레일에서 쓰는 짧은 이름. 없으면 `label`을 쓴다. */
  short?: string;
  active: boolean;
  collapsed: boolean;
  /** 처리를 기다리는 수. 0이면 그리지 않고, 조치가 필요하다는 뜻으로 호박색 글자로 그린다. */
  badge?: number;
  /** 배지 수가 무엇을 세는지 나타내는 이름. 스크린리더가 `3` 대신 `처리 대기 3건`으로 읽는다. */
  badgeLabel?: string;
  /** 배지 수의 단위(`건`·`명`). */
  badgeUnit?: string;
}) {
  const showBadge = badge != null && badge > 0;
  // 수만 읽히면 무엇의 수인지 알 수 없으므로 화면에는 수만 두고 읽는 글자에 이름과 단위를 붙인다.
  const badgeText = showBadge && badgeLabel ? ` · ${badgeLabel} ${badge.toLocaleString("ko-KR")}${badgeUnit}` : null;
  const count = showBadge ? (badge > 999 ? "999+" : badge.toLocaleString("ko-KR")) : null;
  return (
    // 지금 화면인 항목은 색과 함께 `aria-current`로도 알린다.
    <Link to={to} className={navCls(active, collapsed)} aria-current={active ? "page" : undefined}>
      {collapsed ? (
        <>
          {icon}
          <span className="w-full truncate text-center font-medium leading-none">{short ?? label}</span>
          {count && (
            <span aria-hidden={badgeText ? true : undefined} className="font-bold tabular-nums leading-none text-amber">
              {count}
            </span>
          )}
        </>
      ) : (
        <>
          <span
            aria-hidden="true"
            className={cn("size-1.5 shrink-0 rounded-capsule", active ? "bg-amber" : "bg-transparent")}
          />
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {count && (
            <span aria-hidden={badgeText ? true : undefined} className="text-micro font-bold tabular-nums text-amber">
              {count}
            </span>
          )}
        </>
      )}
      {badgeText && <span className="sr-only">{badgeText}</span>}
    </Link>
  );
}
