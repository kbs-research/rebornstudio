/**
 * global_role enum을 한국어 라벨로 바꾼다. 내 계정·역할 페이지가 함께 쓴다.
 * 화면에는 원시 코드(super_admin 등) 대신 라벨을 쓴다. 사전에 없는 값은 원문을 그대로 쓴다.
 */

export const GLOBAL_ROLE_LABEL: Record<string, string> = {
  super_admin: "최고관리자",
  // `일반` 한 낱말로는 역할을 알 수 없으므로 `일반 사용자`로 쓴다. 필터와 표도 같은 이름을 쓴다.
  user: "일반 사용자",
};

/**
 * 셀렉트·필터가 쓰는 순서 있는 목록. 낮은 권한에서 높은 권한 순서다.
 *
 * 목록이 필요하면 이 상수를 가져간다. 화면이 옵션을 다시 적으면 한국어 옵션과 원시 코드 필터가
 * 섞여 같은 열에 `super_admin`이 나온다.
 */
export const GLOBAL_ROLES: readonly string[] = ["user", "super_admin"];

/** 전역 역할 값 하나를 라벨로 바꾼다. 사전에 없는 값은 원문을 쓰고, 값이 없으면 `—`를 쓴다. */
export function globalRoleLabel(v: string | null | undefined): string {
  return v ? (GLOBAL_ROLE_LABEL[v] ?? v) : "—";
}

/**
 * project_role enum의 한국어 라벨. 이 사전이 단일 출처다.
 *
 * 표를 화면마다 복사하면 같은 역할이 화면에 따라 `라벨러`/`라벨`처럼 달라진다.
 */
export const PROJECT_ROLE_LABEL: Record<string, string> = {
  annotator: "라벨러",
  reviewer: "검수자",
  project_admin: "관리자",
  /*
    전역 사전(`GLOBAL_ROLE_LABEL`)과 같은 이름을 쓴다.
    기준 이름은 화면 대부분이 쓰는 `최고관리자`다.
    이름이 다르면 한 사람이 화면마다 다른 호칭으로 불린다.
  */
  super_admin: GLOBAL_ROLE_LABEL.super_admin,
};

/** 프로젝트 역할 값 하나를 라벨로 바꾼다. 사전에 없는 값은 원문을 쓰고, 값이 없으면 `—`를 쓴다. */
export function projectRoleLabel(v: string | null | undefined): string {
  return v ? (PROJECT_ROLE_LABEL[v] ?? v) : "—";
}
