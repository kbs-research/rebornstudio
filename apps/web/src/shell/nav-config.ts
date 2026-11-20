/**
 * 사이드바 내비게이션의 단일 출처.
 *
 * 프로젝트 표면(`SURFACES`) 7개, 개인 표면(`PERSONAL_PATHS`) 2개, 플랫폼 표면(`PLATFORM_ITEMS`,
 * super 전용) 4개를 정한다.
 * 표면 노출은 라우트 권한 표(`lib/roles`의 `ROUTE_ACCESS`)로 정한다. 라우터 가드와 같은 표라서
 * 사이드바가 열 수 없는 화면을 가리키지 않는다.
 *
 * 역할마다 보이는 표면 수가 크게 다르다. 관리자는 7개이고 검수자와
 * 라벨러는 `개요` 하나다(작업자는 `내 작업`에서 자기 큐를 본다).
 * 항목을 더할 때는 그 역할의 사이드바가 몇 줄이 되는지 함께 확인한다.
 */
import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  FileVideo,
  KeyRound,
  LayoutDashboard,
  ShieldCheck,
  Settings,
  Users,
  Workflow,
} from "lucide-react";

import {
  allowsRole,
  type PipelineStage,
  type ProjectRole,
  ROUTE_ACCESS,
  type RouteKey,
} from "@/lib/roles";

export interface SurfaceCtx {
  slug: string;
  role: ProjectRole | null;
  isSuper: boolean;
}

export interface Surface {
  id: string;
  label: string;
  icon: LucideIcon;
  /**
   * 목적지 라우트의 키. 노출은 라우터 가드와 같은 표(`ROUTE_ACCESS`)로 정한다.
   * 없으면 멤버 전원이 본다. 최고관리자는 전 표면을 본다(`surfaceVisible`).
   */
  route?: RouteKey;
  /**
   * 접힘 레일에서 쓰는 짧은 이름. 긴 이름에만 준다.
   *
   * 접힘 레일(73.5px)의 라벨 폭은 53px다(`AppSidebar` 참고). 10px 글자로 이 폭을 넘으면 잘려서
   * (`프로젝트 설정` 54) 무엇의 이름인지 알 수 없다.
   *
   * 레일을 넓히면 접는 의미가 약해지므로 레일 대신 이름을 줄인다.
   * 긴 이름은 몇 개뿐이라 그 항목에만 주고, 주지 않으면 `label`을 그대로 쓴다.
   */
  short?: string;
  to: (ctx: SurfaceCtx) => string;
}

export interface GlobalLink {
  id: string;
  label: string;
  /** 접힘 레일에서 쓰는 짧은 이름(`Surface.short` 참고). */
  short?: string;
  icon: LucideIcon;
  to: string;
}


/**
 * 개인 표면 경로 목록. 사이드바 `내 작업공간`의 활성 판정과 `PersonalTabs`가 함께 쓴다.
 *
 * 두 항목이 사이드바에서 한 항목으로 합쳐져 있어서 지금 개인 표면에 있는지 판정해야 한다.
 * 경로를 두 곳에 적으면 탭은 활성인데 사이드바는 활성이 아닌 상태가 생긴다.
 */
export const PERSONAL_PATHS: readonly string[] = [
  "/my/work", "/my/activity",
];

/**
 * 개인 표면 중 프로젝트에 따라 내용이 바뀌는 경로. 전환기에서 고른 프로젝트로
 * 내용을 거른다. 개인 작업공간의 프로젝트 칩(`PersonalProjectChip`)이 이 목록을 본다.
 */
export const PERSONAL_PROJECT_SCOPED: readonly string[] = [
  "/my/work", "/my/activity",
];

/**
 * 개인 표면의 한국어 이름. 탭과 브레드크럼이 함께 쓴다.
 *
 * 순서는 `PERSONAL_PATHS`가 정하고(쓰는 빈도순으로 할 일, 한 일)
 * 이 상수는 이름만 정한다.
 * `PersonalTabs`와 `breadcrumbs`가 이름을 각자 적으면 한쪽만 고쳤을 때 탭과 머리글의 이름이 달라진다.
 */
export const PERSONAL_LABEL: Record<string, string> = {
  "/my/work": "내 작업",
  "/my/activity": "내 활동",
};


/**
 * 작업 단계의 화면 이름. 브레드크럼이 쓴다.
 *
 * 작업 단계 화면(`features/pipeline/pages/pipeline.tsx`)의 `STAGE_META.label`도 이 표를 읽는다.
 */
export const STAGE_LABEL: Record<PipelineStage, string> = {
  ingest: "영상 등록",
  label: "라벨링",
  review: "검수",
  done: "완료",
};

/**
 * 플랫폼 관리 메뉴(super_admin 전용). 프로젝트 내비게이션과 분리된 영역이다.
 *
 * 같은 곳으로 가는 경로가 두 개면 어느 쪽도 기준이 되지 못하므로 이 항목들을 탭으로 한 번 더 감싸는 화면을 두지 않는다.
 * `개요`는 요약만 보여 주고, 하위 화면으로 가는 링크는 이 메뉴에만 둔다.
 */
export const PLATFORM_ITEMS: GlobalLink[] = [
  { id: "plat-home", label: "플랫폼 개요", short: "개요", icon: LayoutDashboard, to: "/admin" },
  { id: "plat-users", label: "사용자", icon: Users, to: "/admin/users" },
  // 스토리지·분석 자격증명을 넣는 화면이다.
  { id: "plat-connections", label: "자격증명 연결", short: "자격증명", icon: KeyRound, to: "/admin/connections" },
];

export const SURFACES: readonly Surface[] = [
  // 순서는 관리자가 쓰는 흐름이다. 개요에서 막힌 것을 보고, 작업 단계에서 대기열을 보고, 콘텐츠·품질검수에서 처리한다.
  // 사람·통계·기록·설정은 그 뒤에 둔다.
  { id: "overview", label: "개요", icon: LayoutDashboard, to: (c) => `/p/${c.slug}/overview` },
  // 파이프라인 단계는 작업자의 '내 작업'과 같은 워크리스트다(claim 후 워크벤치).
  // 단계 순서는 영상 등록(업로드·MI 분석), 라벨링, 검수, 완료다.
  // 단계별 노출은 실제 수행 권한을 기준으로 `lib/roles`의 `visibleStages`가 정한다.
  /*
    작업 단계는 한 항목이다. `/pipeline?stage=…` 페이지 안에 같은 단계 스텝퍼가 있어서 단계 전환은 스텝퍼가 맡는다.
    단계마다 세는 단위(등록은 영상, 라벨링·검수는 작업, 완료는 클립)가 달라 합계가 의미 없으므로 배지는 달지 않는다.
    역할 노출은 라우트 키(`pipeline`)로 정하고, 그 역할 목록은 네 단계 역할의 합집합이다(`lib/roles.test.ts`가 확인한다).
  */
  {
    id: "stages",
    label: "작업 단계",
    short: "단계",
    icon: Workflow,
    route: "pipeline",
    to: (c) => `/p/${c.slug}/pipeline`,
  },
  { id: "content", label: "콘텐츠", icon: FileVideo, route: "content", to: (c) => `/p/${c.slug}/content` },
  // 보류 건이 0이어도 들어갈 수 있게 사이드바에 둔다. 라우트와 같은 키로 노출한다.
  { id: "qc", label: "캡션 품질검수", short: "품질검수", icon: ShieldCheck, route: "qc", to: (c) => `/p/${c.slug}/qc` },
  { id: "people", label: "멤버", icon: Users, route: "people", to: (c) => `/p/${c.slug}/people` },
  { id: "insights", label: "통계", icon: BarChart3, route: "insights", to: (c) => `/p/${c.slug}/insights` },
  { id: "settings", label: "프로젝트 설정", short: "설정", icon: Settings, route: "settings", to: (c) => `/p/${c.slug}/settings` },
];

/**
 * 작업 단계 항목을 메뉴에서 빼는 역할.
 *
 * 이 역할이 일하는 곳은 `내 작업` 하나이고, 작업 단계 화면은 관리자가 대기열을 보는 곳이다.
 * 라우트 권한(`ROUTE_ACCESS.pipeline`)은 그대로라 주소와 화면 안의 링크(검수 화면의 완료 클립 링크 등)로는 열린다.
 */
export const STAGES_MENU_HIDDEN: readonly ProjectRole[] = ["reviewer"];

/** 표면이 보이면 true를 돌려준다. 최고관리자는 전 표면을, 그 외에는 목적지 라우트를 열 수 있는 역할만 본다. */
export function surfaceVisible(surface: Surface, role: ProjectRole | null, isSuper: boolean): boolean {
  if (isSuper) return true;
  if (role == null) return false;
  if (surface.id === "stages" && STAGES_MENU_HIDDEN.includes(role)) return false;
  return !surface.route || allowsRole(ROUTE_ACCESS[surface.route], null, role);
}

/** 표면 항목의 이름과 목적지를 맥락에 맞춘다. 사이드바가 쓴다. */
export function surfaceLink(surface: Surface, ctx: SurfaceCtx): { label: string; short?: string; to: string } {
  return { label: surface.label, short: surface.short, to: surface.to(ctx) };
}

/** 플랫폼 화면(`/admin`·`/admin/*`)이면 true를 돌려준다. 최고관리자의 사이드바가 플랫폼 메뉴로 바뀐다. */
export function isPlatformPath(pathname: string): boolean {
  return /^\/admin(\/|$)/.test(pathname);
}

/** 플랫폼 메뉴 항목이 지금 화면이면 true를 돌려준다. 개요(`/admin`)는 하위 화면에서 활성이 되지 않도록 경로가 같을 때만 true다. */
export function platformItemActive(to: string, pathname: string): boolean {
  if (to === "/admin") return pathname === "/admin";
  return pathname === to || pathname.startsWith(`${to}/`);
}
