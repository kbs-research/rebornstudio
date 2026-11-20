/**
 * 개인 작업공간(`내 작업`·`내 활동`·`내 계정`)의 서버 호출과 쿼리 키.
 *
 * 화면과 부품은 경로 문자열을 직접 쓰지 않고 이 파일의 함수를 부른다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 주의: 보유 목록·대기 수 키는 작업 단계 화면의 대기열·워크벤치·검수 화면이 같은 배열로
 * 읽고 무효화한다. 값을 바꾸면 한쪽에서 다음 작업을 받아도 다른 쪽 수가 그대로 남는다.
 */
import type { components } from "@reborn/contracts";

import { api, type UserDTO } from "@/shared/api/client";

import type { MyWorkHistoryRow, MyWorkState } from "./lib/my_history";

/** 보유 작업 한 건 */
export type WorkItemDTO = components["schemas"]["WorkItemDTO"];
/** `/work/me`가 주는 본인의 보유 작업 한 건. 재작업이면 마지막 반려 사유(`last_reject_reasons`)가 붙는다. */
export type HeldWorkDTO = components["schemas"]["MyWorkItemDTO"];
/** 라벨링 기록 한 건의 상세. 본인의 제출·캡션과 그 검수 결과가 함께 온다. */
export type MyWorkHistoryDetail = components["schemas"]["MyWorkHistoryDetail"];
/** 본인의 로그인·로그아웃 이력 한 줄 */
export type AuthEvent = components["schemas"]["DomainEventDTO"];
/** 본인이 내린 판정 한 페이지. `total`은 이 페이지의 행 수와 다른 전체 판정 수다. */
export type MyDecisionPage = components["schemas"]["MemberDecisionPage"];
/** 본인이 내린 검수 판정 한 건. */
export type MyDecisionRow = components["schemas"]["MemberDecisionRow"];

/** 서버 대기 수 응답. `count`는 새로 배정할 수 있는 수, `held`는 호출한 사람이 보유한 수다. */
export type AvailableCount = { count: number; held: number };

/** 다음 작업 응답. 보유한 작업이 있으면 그 작업이 `resumed: true`로 온다. */
export type NextWorkDTO = components["schemas"]["NextWorkResponse"];

/** 라벨링 기록 조회의 거르기와 커서. 빈 값은 요청에 싣지 않는다. */
export type WorkHistoryQuery = {
  limit: number;
  q: string;
  state: MyWorkState | "";
  before?: { at: string; id: string };
};

/** 프로필 저장 요청 본문. 선택 정보(전화번호·생년월일·주소)는 받지 않는다. */
export type ProfilePatch = {
  display_name?: string;
};

/**
 * 개인 작업공간의 쿼리 키 팩토리.
 *
 * `…All`은 프로젝트를 뺀 앞부분이다. 보유가 바뀐 뒤 이 앞부분으로 무효화하면 모든 프로젝트의 같은 목록을 다시 받는다.
 * 라벨링 기록의 상세와 대상 목록은 목록 키(`["my-work-history", pid]`) 아래에 둔다. 목록을 무효화하면 둘도 다시 받는다.
 */
export const meKeys = {
  /** 본인이 보유한 라벨링 작업 전체의 앞부분. 보유가 바뀐 뒤 모든 프로젝트의 목록을 무효화한다. */
  heldWorkAll: () => ["work", "me"] as const,
  /** 한 프로젝트에서 본인이 보유한 라벨링 작업 */
  heldWork: (pid: string | null) => ["work", "me", pid] as const,
  workWaitingAll: () => ["work-available-count"] as const,
  /** 라벨링 대기 수·보유 수. */
  workWaiting: (pid: string | null) => ["work-available-count", pid] as const,
  heldReviewsAll: () => ["review", "mine"] as const,
  /** 한 프로젝트에서 본인이 보유한 검수 작업 */
  heldReviews: (pid: string | null | undefined) => ["review", "mine", pid] as const,
  reviewWaitingAll: () => ["review-available-count"] as const,
  /** 검수 대기 수·보유 수. */
  reviewWaiting: (pid: string | null) => ["review-available-count", pid] as const,
  /** 라벨링 기록 목록. 검색어와 상태마다 따로 캐시한다. */
  workHistory: (pid: string, q: string, state: MyWorkState | "") => ["my-work-history", pid, q, state] as const,
  /** 라벨링 기록 한 건의 상세. */
  workHistoryDetail: (pid: string, workItemId: string | undefined) =>
    ["my-work-history", pid, "detail", workItemId] as const,
  /** 본인이 내린 판정 목록. */
  myDecisions: (pid: string) => ["my-decisions", pid] as const,
  /** 본인의 로그인 이력. */
  authEvents: () => ["auth-events", "me"] as const,
};

/** 라벨링 대기 수와 보유 수를 받는다. 서버가 다음 작업과 같은 조건으로 센다. */
export const fetchWorkWaiting = (pid: string | null) => api<AvailableCount>(`/projects/${pid}/work/available-count`);

/** 한 프로젝트에서 보유한 라벨링 작업을 서버가 정한 순서(재작업 먼저, 받은 순)로 받는다. */
export const fetchHeldWork = (pid: string | null) => api<HeldWorkDTO[]>(`/work/me?project_id=${pid}`);

/** 다음 라벨링 작업 한 건을 받는다. 보유한 작업이 있으면 서버가 그 작업을 다시 준다. */
export const nextWork = (pid: string | null) => api<NextWorkDTO>(`/projects/${pid}/work/next`, { method: "POST" });

/** 검수 대기 수와 보유 수를 받는다. 서버가 다음 검수와 같은 조건으로 센다. */
export const fetchReviewWaiting = (pid: string | null) =>
  api<AvailableCount>(`/projects/${pid}/review/available-count`);

/** 한 프로젝트에서 보유한 검수 작업을 받는다. */
export const fetchHeldReviews = (pid: string | null | undefined) => api<WorkItemDTO[]>(`/projects/${pid}/review/mine`);

/** 다음 검수 작업 한 건을 받는다. 보유한 검수가 있으면 서버가 그 작업을 다시 준다. */
export const nextReview = (pid: string | null) =>
  api<NextWorkDTO>(`/projects/${pid}/review/next`, { method: "POST" });

/** 프로젝트에서 나간다. 서버가 보유한 라벨링 작업을 대기열로 돌리고 그 수를 돌려준다. 사유가 비면 `null`을 보낸다. */
export const leaveProject = (pid: string, reason: string | null) =>
  api<{ released: number }>(`/projects/${pid}/me/leave`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

/**
 * 비밀번호를 바꾼다. 서버가 이전 토큰을 모두 막는다.
 *
 * 이 요청의 401은 "현재 비밀번호가 틀렸다"는 뜻이다. `expect401`이 없으면 `api()`가 세션 만료로 읽는다.
 */
export const changeMyPassword = (oldPassword: string, newPassword: string) =>
  api<void>("/auth/password/change", {
    method: "POST",
    expect401: true,
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });

/** 본인의 로그인·로그인 실패·로그아웃 이력을 최근 것부터 `limit`건 받는다. */
export const fetchMyAuthEvents = (limit: number) => api<AuthEvent[]>(`/me/auth-events?limit=${limit}`);

/** 본인 프로필을 저장한다. 서버가 다듬은 값을 돌려준다. */
export const updateMyProfile = (patch: ProfilePatch) =>
  api<UserDTO>("/users/me", { method: "PATCH", body: JSON.stringify(patch) });

/** 이전에 한 선택 동의를 철회한다. 서버가 선택 정보를 바로 지우고 동의 시각을 비운다. */
export const withdrawOptional = () => api<UserDTO>("/users/me/optional-privacy-consent", { method: "DELETE" });

/**
 * 본인이 내린 검수 판정을 최신순으로 한 페이지 받는다.
 *
 * 다음 페이지는 앞 페이지 마지막 행의 판정 시각과 id를 커서로 보낸다. 시각만 보내면 같은 시각의 판정이 빠진다.
 */
export function fetchMyDecisions(pid: string, limit: number, cursor?: { before: string; beforeId: string }) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    q.set("before", cursor.before);
    q.set("before_id", cursor.beforeId);
  }
  return api<MyDecisionPage>(`/projects/${pid}/me/decisions?${q}`);
}

/** 라벨링 기록 한 페이지를 받는다. 검색과 상태 거르기는 서버가 전체 기록에 적용한다. 커서는 서버가 정렬에 쓴 시각이다. */
export const fetchMyWorkHistory = (pid: string, { limit, q, state, before }: WorkHistoryQuery) =>
  api<MyWorkHistoryRow[]>(
    `/projects/${pid}/me/work-history?limit=${limit}` +
      (q ? `&q=${encodeURIComponent(q)}` : "") +
      (state ? `&state=${state}` : "") +
      (before ? `&before=${encodeURIComponent(before.at)}&before_id=${before.id}` : ""),
  );

/**
 * 라벨링 기록 한 건의 상세를 받는다.
 *
 * `/work/{id}/labels`는 지금 보유한 사람과 검수자에게만 열려 다른 라벨러에게 넘어간 작업에서 403이다.
 * 이 조회는 본인 기록 전용이라 작업이 넘어간 뒤에도 본인이 작업한 부분을 연다.
 */
export const fetchMyWorkHistoryDetail = (pid: string, workItemId: string) =>
  api<MyWorkHistoryDetail>(`/projects/${pid}/me/work-history/${workItemId}`);
