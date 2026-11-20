/**
 * 프로젝트 역할 목록. 멤버 표의 초대 창과 사람 서랍의 설정 탭이 함께 쓴다.
 *
 * `lib/roles.ts`에도 DTO에서 뽑은 같은 이름의 `ProjectRole`이 있다. 값은 같고 출처가 다르다.
 */
/* 순서는 업무 흐름이다. 관리, 라벨링, 검수 순으로 둔다. */
export const ROLES = ["project_admin", "annotator", "reviewer"] as const;
export type ProjectRole = (typeof ROLES)[number];

/**
 * 역할마다 이 프로젝트에서 하는 일을 한 문장으로 쓴다. 초대 창의 역할 고르개 아래에 보인다.
 *
 * 기준은 라우터 `RoleGuard`와 `shell/nav-config`다. 그쪽을 고치면 이 문장도 확인한다.
 * 초대 창에서 역할을 고르는 사람이 창을 닫지 않고 그 역할이 하는 일을 읽게 한다.
 */
export const ROLE_DESC: Record<ProjectRole, string> = {
  project_admin: "대기열·설정·멤버를 관리하고 모든 작업을 할 수 있습니다.",
  annotator: "내 작업에서 라벨링 작업을 한 건씩 받아 제출합니다.",
  reviewer: "제출된 작업을 검수합니다.",
};
