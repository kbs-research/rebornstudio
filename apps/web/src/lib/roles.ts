/**
 * 권한 판정 헬퍼와 권한 표. 화면의 역할 가드가 읽는 단일 출처.
 *
 * 서버 가드와 같은 규칙으로 판정하는 순수 함수와 표다. 권한이 없는 사용자에게 빈 화면이나
 * 원문 에러 대신 접근 안내를 보여 주고, 열 수 있는 화면으로 가는 링크만 그리게 한다.
 * 라우터 가드(`RoleGuard`), 사이드바(`nav-config`), 링크(`RoleLink`), 첫 화면(`features/me/pages/home.tsx`),
 * 접근 안내(`AccessDenied`)가 이 파일의 표를 같이 읽는다.
 */
import type { ProjectDTO, UserDTO } from "@/shared/api/client";
import { hasFinalConsonant } from "@/shared/lib/josa";

export type GlobalRole = NonNullable<UserDTO["global_role"]>;
export type ProjectRole = NonNullable<ProjectDTO["my_role"]>;

/** 전역 super_admin이면 true를 돌려준다. super_admin은 모든 프로젝트 권한 검사를 통과한다. */
export function isSuperAdmin(user: UserDTO | null): boolean {
  return user?.global_role === "super_admin";
}

/** user의 전역 역할이 allowed 중 하나이면 true를 돌려준다. */
export function hasGlobalRole(user: UserDTO | null, allowed: readonly GlobalRole[]): boolean {
  return user?.global_role != null && allowed.includes(user.global_role);
}

/** user의 해당 프로젝트 역할이 allowed 중 하나이면 true를 돌려준다. super_admin은 항상 통과한다. */
export function hasProjectRole(
  user: UserDTO | null,
  project: ProjectDTO | undefined,
  allowed: readonly ProjectRole[],
): boolean {
  if (isSuperAdmin(user)) return true;
  const role = project?.my_role;
  return role != null && allowed.includes(role);
}

/**
 * 작업자 역할. 큐에서 작업을 받아 처리하는 역할이며 프로젝트 관리자와 구분한다.
 *
 * 이 목록은 사이드바의 노출 매트릭스와 같은 사실을 나타낸다.
 * 작업자는 프로젝트 화면 중 `개요`와 일부 화면만 보고, 실제 일은 `내 작업`에서 한다.
 */
const WORKER_ROLES: readonly ProjectRole[] = ["annotator", "reviewer"];

/**
 * 파이프라인 라우트를 열 수 있는 역할. 라벨러(annotator)만 뺀 전부다.
 *
 * 라벨러는 자기 큐(`내 작업`)만 보면 되고, 파이프라인에는 다른 사람의 진행 상황처럼 작업에 필요 없는 정보가 실린다.
 * 어느 단계를 보는지는 아래 `STAGE_ROLES`가 정한다.
 */
export const NON_ANNOTATOR_PROJECT_ROLES: readonly ProjectRole[] = ["project_admin", "reviewer"];

/**
 * 작업의 산출물을 보는 역할. 완료 목록·완료 상세·영상 상세에 쓴다.
 *
 * 서버 쪽 기준 `authz._WORK_RESULT_VIEWER_ROLES`를 옮긴 목록이다.
 * 두 층이 각자 적으면 결과가 달라지므로, 같은 내용을 여는 화면은 모두 이 목록 하나로 막는다.
 * 이 목록을 서버보다 넓히면 열자마자 403이 되고, 좁히면 볼 수 있는 화면으로 가는 경로가 없어지므로 서버를 따른다.
 */
export const WORK_RESULT_VIEWER_ROLES: readonly ProjectRole[] = ["project_admin", "reviewer"];

/**
 * 관리 표면(콘텐츠·멤버·감사 로그·캡션 품질검수)을 여는 역할.
 *
 * 서버 `authz.require_project_admin`과 같은 목록이다.
 */
export const MANAGER_ROLES: readonly ProjectRole[] = ["project_admin"];

/**
 * 통계를 여는 역할. 관리자다.
 *
 * 통계 화면은 사람별 실적 표이고 그 API(`labeler-performance`)는 관리자 전용이다.
 * 화면을 서버보다 넓게 열면 오류 카드만 남은 화면이 생긴다.
 */
export const INSIGHTS_ROLES: readonly ProjectRole[] = ["project_admin"];

/** 검수 워크벤치를 여는 역할. 서버 `work.review._REVIEWER_ROLES`와 같다. */
export const REVIEWER_ROLES: readonly ProjectRole[] = ["reviewer", "project_admin"];

/**
 * 막힌 사람에게 보여 줄 문구. 없으면 `deniedCopy`가 역할 목록으로 만든다.
 *
 * `message`는 굵은 결과 아래 첫 문장이고 `hint`는 다음 할 일이다.
 * `hint: null`이면 안내 줄을 그리지 않는다.
 */
export interface DeniedCopy {
  message?: string;
  hint?: string | null;
}

/** 막힌 사람을 역할의 첫 화면 대신 보낼 곳. */
export interface Destination {
  to: string;
  label: string;
}

/** 라우트 하나의 권한. `scope`가 어느 역할(플랫폼·프로젝트)을 보는지 정한다. */
export type RouteAccess =
  | { scope: "global"; roles: readonly GlobalRole[]; denied?: DeniedCopy }
  | { scope: "project"; roles: readonly ProjectRole[]; denied?: DeniedCopy; alt?: Destination };

/**
 * 라우트 권한 표. 라우터 가드와 링크를 그리는 화면이 같은 표를 읽는다.
 *
 * 키 하나가 `router.tsx`의 `RoleGuard` 묶음 하나이고, 역할 목록은 서버 가드를 옮긴 목록이다.
 * 화면이 링크를 그릴 때는 `RoleLink`나 `canOpenRoute`로 같은 키를 조회한다.
 * 권한이 가드마다 인라인 배열로 흩어지면 링크를 그리는 화면이 조회할 곳이 없어,
 * 열 수 없는 화면으로 가는 버튼과 링크가 보인다.
 */
export const ROUTE_ACCESS = {
  /** 플랫폼 관리(`/admin/*`). */
  platform: { scope: "global", roles: ["super_admin"] },
  /** 프로젝트 추가(`/projects/new`). 조회 화면과 달리 만드는 화면이라 안내 문구를 따로 둔다. */
  projectNew: {
    scope: "global",
    roles: ["super_admin"],
    denied: { message: "프로젝트는 최고관리자만 추가할 수 있습니다.", hint: "필요하면 최고관리자에게 요청해주세요." },
  },
  /** 통계(`/p/:slug/insights`). */
  insights: { scope: "project", roles: INSIGHTS_ROLES },
  /** 멤버(`/p/:slug/people`). */
  people: { scope: "project", roles: MANAGER_ROLES },
  /** 캡션 품질검수(`/p/:slug/qc`). 서버 라우트와 같은 권한 축이다. */
  qc: { scope: "project", roles: MANAGER_ROLES },
  /** 영상·클립 카탈로그(`/p/:slug/content`). */
  content: { scope: "project", roles: MANAGER_ROLES },
  /** `/p/:slug/settings` 외 거버넌스 표면. */
  settings: { scope: "project", roles: ["project_admin"] },
  /**
   * 작업 단계(`/p/:slug/pipeline`). 라벨러만 막힌다.
   * 라벨러에게는 권한 요청 대신 일하는 곳을 안내한다.
   */
  pipeline: {
    scope: "project",
    roles: NON_ANNOTATOR_PROJECT_ROLES,
    denied: { message: "라벨링 작업은 내 작업에서 합니다.", hint: null },
  },
  /** 작업 산출물(`/p/:slug/completed[/:id]`·`/p/:slug/videos/:id`). */
  workResult: { scope: "project", roles: WORK_RESULT_VIEWER_ROLES },
  /** 검수 워크벤치(`/review/:id`). */
  review: { scope: "project", roles: REVIEWER_ROLES },
} as const satisfies Record<string, RouteAccess>;

export type RouteKey = keyof typeof ROUTE_ACCESS;

/** 역할 두 개(플랫폼·프로젝트)만으로 라우트를 열 수 있으면 true를 돌려준다. 브레드크럼처럼 사용자 객체가 없는 곳이 쓴다. */
export function allowsRole(
  access: RouteAccess,
  globalRole: string | null | undefined,
  role: string | null | undefined,
): boolean {
  if (access.scope === "global") {
    return globalRole != null && (access.roles as readonly string[]).includes(globalRole);
  }
  if (globalRole === "super_admin") return true;
  return role != null && (access.roles as readonly string[]).includes(role);
}

/** 이 사람이 이 라우트를 열 수 있으면 true를 돌려준다. 프로젝트 범위 라우트는 넘긴 프로젝트의 역할로 판정한다. */
export function canOpenRoute(key: RouteKey, user: UserDTO | null, project: ProjectDTO | undefined): boolean {
  return allowsRole(ROUTE_ACCESS[key], user?.global_role, project?.my_role);
}

/**
 * 안내 문장에 역할을 나열하는 순서. 권한이 넓은 역할부터 쓴다.
 *
 * 표마다 적힌 순서가 달라서 그대로 쓰면 화면마다 같은 역할이 다른 위치에 온다.
 */
const ROLE_ORDER: readonly string[] = ["super_admin", "project_admin", "reviewer", "annotator"];

/** 역할 코드를 `ROLE_ORDER` 순서로 정렬한다. 표에 없는 코드는 뒤로 보낸다. */
function sortRoles(roles: readonly string[]): string[] {
  const rank = (r: string) => (ROLE_ORDER.includes(r) ? ROLE_ORDER.indexOf(r) : ROLE_ORDER.length);
  return [...roles].sort((a, b) => rank(a) - rank(b));
}

/** 역할 이름을 문장에 넣는 꼴로 잇는다. 둘이면 `A와 B`, 셋 이상이면 `A·B·C`다. */
export function joinRoleNames(names: readonly string[]): string {
  if (names.length === 2) return `${names[0]}${hasFinalConsonant(names[0]) ? "과" : "와"} ${names[1]}`;
  return names.join("·");
}

/**
 * 막힌 라우트의 안내 문구를 만든다. 누가 이 화면을 여는지와 권한을 누구에게 요청하는지를 쓴다.
 *
 * 표에 `denied`가 있으면 그 값을 우선한다. 역할 이름은 `labelOf`(화면 사전)로 옮긴다.
 * 스스로 참여를 요청하는 화면이 없으므로 기본 안내는 관리자에게 권한을 요청하라는 문장이다.
 * 모든 막힘을 `프로젝트 관리자에게 문의하세요` 한 문구로 쓰면 누가 여는 화면인지 알 수 없다.
 */
export function deniedCopy(
  access: RouteAccess,
  labelOf: (role: string) => string,
): { message: string; hint: string | null } {
  const who = joinRoleNames(sortRoles(access.roles).map(labelOf));
  const asker = access.scope === "global" ? labelOf("super_admin") : "프로젝트 관리자";
  const denied: DeniedCopy = access.denied ?? {};
  const message = denied.message ?? `${who}만 볼 수 있는 화면입니다.`;
  const hint = denied.hint !== undefined ? denied.hint : `권한이 필요하면 ${asker}에게 요청해주세요.`;
  return { message, hint };
}

/** 파이프라인 단계. 순서는 공정 순서(등록, 라벨링, 검수, 완료)다. */
export const PIPELINE_STAGES = ["ingest", "label", "review", "done"] as const;
export type PipelineStage = (typeof PIPELINE_STAGES)[number];

/**
 * 단계마다 그 단계를 보는 역할. 작업 단계 화면(`features/pipeline/pages/pipeline.tsx`)·사이드바·브레드크럼이
 * `visibleStages`·`landingStage`로 이 표를 읽는다.
 *
 * `done`만 수행 대신 열람 권한이라서 `WORK_RESULT_VIEWER_ROLES`와 같다.
 */
export const STAGE_ROLES: Record<PipelineStage, readonly ProjectRole[]> = {
  ingest: ["project_admin"],
  label: ["project_admin"],
  review: ["project_admin", "reviewer"],
  done: WORK_RESULT_VIEWER_ROLES,
};

/** 이 역할이 보는 단계를 공정 순서대로 돌려준다. 최고관리자는 전부 본다. */
export function visibleStages(role: string | null | undefined, isSuper: boolean): PipelineStage[] {
  if (isSuper) return [...PIPELINE_STAGES];
  if (role == null) return [];
  return PIPELINE_STAGES.filter((s) => (STAGE_ROLES[s] as readonly string[]).includes(role));
}

/**
 * `?stage=` 없이 들어왔을 때 처음 열 단계를 돌려준다. 목록 순서는 공정 순서라서 착지만 역할로 정한다.
 *
 * `stages[0]`을 착지로 쓰면 검수자가 할 수 없는 `영상 등록`이 열린다.
 */
export function landingStage(role: string | null | undefined, stages: readonly PipelineStage[]): PipelineStage | undefined {
  const primary: PipelineStage | undefined = role === "reviewer" ? "review" : undefined;
  return primary && stages.includes(primary) ? primary : stages[0];
}

/**
 * 이 사람이 어느 프로젝트에서도 관리자가 아니면 true를 돌려준다.
 * 홈을 역할의 첫 화면으로 보낼지 정하는 데 쓴다.
 *
 * 프로젝트 목록이 홈이면 라벨러는 카드 하나를 거쳐 다시 `내 작업`으로 가야 한다.
 * 한 곳이라도 관리를 맡고 있으면 프로젝트를 고르는 일부터 하므로 목록을 홈으로 둔다.
 *
 * 멤버십이 하나도 없으면 보낼 작업이 없으므로 `false`를 돌려주고 목록(과 빈 상태)을 보여 준다.
 */
function isWorkerOnly(user: UserDTO | null, projects: ProjectDTO[] | undefined): boolean {
  if (isSuperAdmin(user)) return false;
  if (!projects?.length) return false;
  return projects.every((p) => p.my_role != null && WORKER_ROLES.includes(p.my_role));
}

/** 역할의 첫 화면. `label`은 버튼과 안내가 부르는 이름이다. */
export interface Landing {
  to: string;
  label: string;
}

/** 프로젝트 목록. 관리자·최고관리자의 첫 화면이고, 어느 역할이든 열 수 있다. */
export const PROJECT_LIST_LANDING: Landing = { to: "/projects", label: "프로젝트 목록" };

/**
 * 역할의 첫 화면을 정한다. 홈(`/`)과 접근 안내(`AccessDenied`)가 같이 읽는다.
 *
 * - 라벨러·검수자: `내 작업`. 큐는 `/my/work`가 역할로 고른다.
 * - 관리자·최고관리자, 역할이 없는 사람: 프로젝트 목록.
 */
export function landingFor(role: string | null | undefined, isSuper: boolean): Landing {
  if (isSuper || role == null || role === "project_admin") return PROJECT_LIST_LANDING;
  return { to: "/my/work", label: "내 작업" };
}

/**
 * 홈(`/`)이 보낼 곳을 돌려준다. `null`이면 홈 주소에서 프로젝트 목록을 그린다.
 *
 * 어느 프로젝트에서도 관리자가 아닌 사람만 보낸다(`isWorkerOnly`). 역할은 고른 프로젝트의 역할을 쓰고,
 * 고른 프로젝트가 목록에 없으면 첫 프로젝트의 역할을 쓴다.
 * `projectId`는 그 역할을 읽은 프로젝트다. 홈은 이동하기 전에 그 프로젝트를 고른 상태로 둔다.
 * 고르지 않으면 `내 작업`이 프로젝트 없이 열려 `프로젝트 선택 필요`에서 멈춘다.
 */
export function homeLanding(
  user: UserDTO | null,
  projects: ProjectDTO[] | undefined,
  selectedProjectId: string | null,
): (Landing & { projectId: string }) | null {
  if (!projects?.length || !isWorkerOnly(user, projects)) return null;
  const p = projects.find((x) => x.project_id === selectedProjectId) ?? projects[0];
  return { ...landingFor(p.my_role, false), projectId: p.project_id };
}

/** 목적지 이름을 이동 버튼 문구로 만든다. 예: `내 작업`은 `내 작업으로 이동`, `내 활동`은 `내 활동으로 이동`. */
export function moveLabel(label: string): string {
  const last = label.trim().slice(-1);
  const code = last.charCodeAt(0) - 0xac00;
  // 한글 받침 ㄹ(종성 번호 8) 뒤에는 `로`를 쓴다.
  const rieul = code >= 0 && code <= 11171 && code % 28 === 8;
  return `${label}${!hasFinalConsonant(label) || rieul ? "로" : "으로"} 이동`;
}
