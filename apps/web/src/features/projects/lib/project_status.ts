/**
 * 프로젝트 상태(`ProjectStatus`)의 화면 이름. 프로젝트 목록 배지와 설정의 상태 선택지가 함께 쓴다.
 *
 * 이름을 이 파일 한 곳에 두고 두 화면이 가져간다. 계약의 세 값을 모두 적는다.
 * 하나라도 빠지면 그 상태의 프로젝트가 목록에서 빈 칩으로 그려진다.
 * 끝난 프로젝트는 `종료` 하나다. DB에 남은 `archived`는 서버가 `completed`로 내보낸다.
 */
import type { components } from "@reborn/contracts";

export type ProjectStatus = components["schemas"]["ProjectDTO"]["status"];

export const PROJECT_STATUS_LABEL: Record<ProjectStatus, string> = {
  active: "활성",
  paused: "일시정지",
  completed: "종료",
};

/** 선택지·필터 순서. 운영 중, 멈춤, 끝 순서다. */
export const PROJECT_STATUS_ORDER: readonly ProjectStatus[] = ["active", "paused", "completed"];
