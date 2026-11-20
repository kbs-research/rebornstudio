/**
 * 경로를 브레드크럼으로 바꾼다. 상단바(`TopBar`)가 모든 화면에서 같은 규칙으로 현재 위치를 그린다.
 *
 * 페이지가 브레드크럼을 각자 넘기면 화면마다 있고 없음이 달라지므로 경로와 프로젝트 맥락(`CrumbContext`)으로 만든다.
 * 이름은 사이드바 설정(`nav-config`)에서 가져온다. 두 곳에 적으면 한쪽만 고쳐진다.
 * 부모 마디는 그 역할이 열 수 있을 때만 링크로 둔다. 판정은 라우터 가드와 같은 표(`lib/roles`의 `ROUTE_ACCESS`)다.
 * 이름이 정해지지 않은 경로는 원문 세그먼트 대신 `UNKNOWN_LABEL`(본문 제목과 같은 `페이지를 찾을 수 없음`)을 쓴다.
 * 브레드크럼 타입도 여기 둔다.
 */
import {
  allowsRole,
  landingStage,
  PIPELINE_STAGES,
  type PipelineStage,
  PROJECT_LIST_LANDING,
  ROUTE_ACCESS,
  type RouteKey,
  visibleStages,
} from "@/lib/roles";

import { PERSONAL_LABEL, PERSONAL_PATHS, PLATFORM_ITEMS, STAGE_LABEL } from "./nav-config";

/** 브레드크럼 한 마디. `to`가 없으면 현재 위치(링크 아님)다. */
export interface BreadcrumbItem {
  label: string;
  to?: string;
  /** 이름을 아직 받지 못했으면 true. 상단바는 이름 대신 골격을 그린다. */
  pending?: boolean;
}

/** 브레드크럼을 만드는 데 필요한 맥락. 상단바가 프로젝트 목록과 계정에서 채운다. */
export interface CrumbContext {
  /** 주소 slug의 프로젝트 이름. `undefined`는 목록을 받는 중, `null`은 목록에 없음이다. */
  projectName?: string | null;
  /** 그 프로젝트에서의 역할. `undefined`는 아직 받지 못함, `null`은 멤버가 아님이다. */
  role?: string | null;
  /** 플랫폼 역할. 최고관리자는 프로젝트 역할과 상관없이 모든 화면을 연다. */
  globalRole?: string | null;
  /** 화면이 올린 이름(`useDocumentTitle`). 상세 화면의 끝 마디가 쓴다. */
  pageTitle?: string | null;
}

/**
 * 이름이 정해지지 않은 경로의 이름. 본문의 없는 주소 안내(`RouteError`)와 같은 이름을 쓴다.
 *
 * 없는 주소(`/my/unknown`)에서 원문 세그먼트 `my`가 영문 그대로 찍히지 않게 한다.
 */
export const UNKNOWN_LABEL = "페이지를 찾을 수 없음";

/** 프로젝트 목록 마디의 이름. 목적지는 역할의 첫 화면 표의 프로젝트 목록이다. */
const PROJECT_ROOT = "프로젝트";

/**
 * 목록에 없는 프로젝트 주소의 끝 마디.
 *
 * 본문은 셸이 `프로젝트 없음` 안내로 막는다. 주소의 화면 이름(`개요` 등)을 쓰면 없는 화면이 열린 것처럼 읽힌다.
 */
const MISSING_PROJECT = "프로젝트 없음";

/** 프로젝트 표면 세그먼트의 이름. */
const SECTION_LABEL: Record<string, string> = {
  overview: "개요",
  content: "콘텐츠",
  // 사이드바 항목 이름과 같은 말이다.
  pipeline: "작업 단계",
  people: "멤버",
  insights: "통계",
  // `설정`만으로는 무엇의 설정인지 알 수 없으므로 이름을 바꾼다.
  settings: "프로젝트 설정",
  // 완료 워크벤치(`/p/:slug/completed`). 없으면 원문 `completed`가 영문으로 보인다.
  completed: "완료 클립",
  qc: "캡션 품질검수",
};

/**
 * `/admin/*` 세그먼트의 이름. 사이드바 계정 메뉴(`PLATFORM_ITEMS`)에서 만든다.
 *
 * 손으로 다시 적은 사전을 두면 항목이 빠져 원문 세그먼트(`/admin/users`의 `users`)가 뜬다.
 */
const ADMIN_LABEL: Record<string, string> = {
  ...Object.fromEntries(
    PLATFORM_ITEMS.filter((i) => i.to.startsWith("/admin/"))
      .map((i) => [i.to.split("/")[2], i.label] as const),
  ),
};

/** 플랫폼 관리 영역의 이름. 페이지 제목(`플랫폼 개요`)과 사이드바 머리글(`플랫폼`)과 같은 말이다. */
const PLATFORM_AREA = "플랫폼";

/** 최상위 단일 경로의 이름. 워크벤치 주소는 전체 화면이라 탭 제목의 대체 이름으로 쓰인다. */
const TOP_LABEL: Record<string, string> = {
  account: "내 계정",
  review: STAGE_LABEL.review,
};

/**
 * 파이프라인 단계 마디의 이름을 고른다. 파이프라인 화면과 같은 규칙으로 단계를 고른다.
 *
 * 주소의 단계를 그 역할이 볼 수 있으면 그 단계 이름을, 볼 수 없으면 역할의 착지 단계 이름을 쓴다.
 * 파이프라인 화면은 그런 주소를 착지 단계로 바꿔 연다.
 * 볼 단계가 없으면(라벨러) 막힌 화면을 `라벨링` 화면으로 표시하지 않도록 `작업 단계`를 쓴다.
 * 모든 단계(영상 등록·완료 포함)에 이름이 있어야 `작업 단계`로 바뀌지 않는다.
 */
function stageLabel(search: string, ctx: CrumbContext): string {
  const requested = (new URLSearchParams(search).get("stage") ?? "") as PipelineStage;
  const known = (PIPELINE_STAGES as readonly string[]).includes(requested);
  const isSuper = ctx.globalRole === "super_admin";
  if (ctx.role === undefined && !isSuper) return known ? STAGE_LABEL[requested] : SECTION_LABEL.pipeline;
  const stages = visibleStages(ctx.role, isSuper);
  if (known && stages.includes(requested)) return STAGE_LABEL[requested];
  const landing = landingStage(ctx.role, stages);
  return landing ? STAGE_LABEL[landing] : SECTION_LABEL.pipeline;
}

/** 프로젝트 화면(`/p/:slug/...`)의 브레드크럼을 만든다. */
function projectCrumbs(seg: string[], search: string, ctx: CrumbContext): BreadcrumbItem[] {
  const slug = seg[1];
  const section = seg[2];
  const isSuper = ctx.globalRole === "super_admin";
  const known = ctx.role !== undefined || isSuper;
  /** 이 역할이 라우트를 열 수 있으면 true. 역할 정보가 없으면 열 수 없다고 보고 링크를 두지 않는다. */
  const canOpen = (key: RouteKey) => known && allowsRole(ROUTE_ACCESS[key], ctx.globalRole, ctx.role);
  /** 이 역할이 파이프라인 단계를 보면 true. */
  const seesStage = (stage: PipelineStage) => known && visibleStages(ctx.role, isSuper).includes(stage);

  // 목록 마디는 프로젝트 목록으로 간다. `/`로 보내면 작업자는 `/my/work`로 이동한다.
  const crumbs: BreadcrumbItem[] = [{ label: PROJECT_ROOT, to: PROJECT_LIST_LANDING.to }];
  // 이름은 서버가 준 값만 쓴다. 받기 전에는 slug 대신 골격을 둔다.
  if (ctx.projectName === undefined) crumbs.push({ label: "", pending: true });
  else if (ctx.projectName === null) return [...crumbs, { label: MISSING_PROJECT }];
  else crumbs.push({ label: ctx.projectName, to: `/p/${slug}/overview` });
  if (!section) return crumbs;

  /*
    상세 화면은 부모 마디를 링크로 두고 끝 마디에 대상의 이름을 쓴다.
    부모를 열 수 없는 역할에게는 부모 마디를 빼고, 대상 이름은 화면이 올린 제목을 쓴다.
  */
  if (section === "videos" && seg[3]) {
    if (canOpen("content")) crumbs.push({ label: SECTION_LABEL.content, to: `/p/${slug}/content` });
    crumbs.push({ label: ctx.pageTitle ?? (seg[4] === "clips" ? "클립 보기" : "영상 상세") });
    return crumbs;
  }
  if (section === "completed" && seg[3]) {
    if (seesStage("done")) crumbs.push({ label: STAGE_LABEL.done, to: `/p/${slug}/pipeline?stage=done` });
    crumbs.push({ label: ctx.pageTitle ?? SECTION_LABEL.completed });
    return crumbs;
  }

  let label: string;
  if (section === "pipeline") label = stageLabel(search, ctx);
  else label = SECTION_LABEL[section] ?? UNKNOWN_LABEL;
  crumbs.push({ label });
  return crumbs;
}

/** 경로(+`search`)와 맥락으로 브레드크럼 배열을 만든다. 마지막 항목이 현재 화면이고 링크가 없다. */
export function resolveBreadcrumbs(pathname: string, search = "", ctx: CrumbContext = {}): BreadcrumbItem[] {
  const seg = pathname.split("/").filter(Boolean);
  if (seg.length === 0) return [{ label: PROJECT_ROOT }];
  // 끝 슬래시를 지운 경로. 개인 표면은 세그먼트가 아니라 경로 전체로 맞춘다.
  const path = "/" + seg.join("/");

  if (seg[0] === "p" && seg[1]) return projectCrumbs(seg, search, ctx);

  /*
    개인 작업공간 화면은 사이드바가 모두 `내 작업공간`으로 표시하므로 브레드크럼도 같은 부모를 단다.
  */
  if (PERSONAL_PATHS.includes(path)) {
    return [{ label: "내 작업공간" }, { label: PERSONAL_LABEL[path] ?? UNKNOWN_LABEL }];
  }

  // 플랫폼 관리. 영역 이름은 사이드바 머리글·계정 메뉴와 같은 `플랫폼`이다.
  if (seg[0] === "admin") {
    // `/admin` 자신이 플랫폼 첫 화면이라 마디를 한 번만 쓴다.
    if (!seg[1]) return [{ label: PLATFORM_AREA }];
    return [{ label: PLATFORM_AREA, to: "/admin" }, { label: ADMIN_LABEL[seg[1]] ?? UNKNOWN_LABEL }];
  }

  // 프로젝트 목록과 추가 화면. 추가 화면은 목록을 부모로 둔다.
  // 이름이 없으면 원문 `projects`가 영문으로 보인다. 프로젝트를 만드는 첫 화면이라 앱의 첫인상이 된다.
  if (seg[0] === "projects") {
    if (!seg[1]) return [{ label: PROJECT_ROOT }];
    return [
      { label: PROJECT_ROOT, to: PROJECT_LIST_LANDING.to },
      { label: seg[1] === "new" && seg.length === 2 ? "프로젝트 추가" : UNKNOWN_LABEL },
    ];
  }

  // `/work/:id/label`은 라벨링 워크벤치다.
  if (seg[0] === "work" && seg[1]) return [{ label: STAGE_LABEL.label }];

  if (TOP_LABEL[seg[0]]) return [{ label: TOP_LABEL[seg[0]] }];

  return [{ label: UNKNOWN_LABEL }];
}
