/**
 * 라벨링 워크벤치와 완료 워크벤치의 서버 호출과 쿼리 키.
 *
 * 화면과 훅은 경로 문자열을 직접 두지 않고 이 파일의 함수를 부른다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 주의: 키 배열의 값은 바꾸지 않는다. 작업 한 건·라벨·검수 키는 검수 워크벤치가 같은 배열로 읽고,
 * 완료 요약 키는 작업 단계의 완료 표가 같은 배열로 읽는다. 값이 다르면 같은 요청을 두 번 보내고 무효화가 한쪽 화면을 갱신하지 못한다.
 */
import type { components } from "@reborn/contracts";

import { api, type LabelDTO, type ReviewDTO } from "@/shared/api/client";

/** 작업 한 건. */
type WorkItemDTO = components["schemas"]["WorkItemDTO"];
/** 완료 목록 한 페이지. */
type CompletedPage = components["schemas"]["CompletedPage"];

/**
 * 완료 목록 머리에 쓰는 전수 집계(`GET …/completed/summary`).
 * 생성물(`@reborn/contracts`)의 `CompletedSummary`와 같은 필드를 이 파일에서 직접 정의한다.
 */
export type CompletedSummary = {
  total: number;
  deliverable_total?: number | null;
  deliverable_scan_limit: number;
  qc_held_total: number;
  qc_unreviewed_total: number;
};

/**
 * 워크벤치의 쿼리 키 팩토리.
 *
 * `all`은 작업 키 전체의 앞부분이다. 제출·신고 뒤 이 앞부분으로 무효화하면 `내 작업`의 보유 목록(`["work", "me", …]`)까지 다시 받는다.
 */
export const workbenchKeys = {
  all: () => ["work"] as const,
  /** 작업 한 건. */
  item: (id: string | undefined) => ["work", "one", id] as const,
  /** 작업 한 건의 라벨 제출 이력. */
  labels: (id: string | undefined) => ["work", "labels", id] as const,
  /** 작업 한 건의 검수 이력. */
  reviews: (id: string | undefined) => ["work", "reviews", id] as const,
  /** 완료 워크벤치 목록. 고른 프로젝트·검색어·필터마다 따로 둔다. */
  completedList: (pid: string | null, query: string, filter: string) => ["completed-list", pid, query, filter] as const,
  /** 완료 전수 집계. 작업 단계의 완료 표와 캐시를 같이 쓴다. */
  completedSummary: (pid: string | null) => ["completed-summary", pid] as const,
};

/** 작업 한 건을 받는다. */
export const fetchWorkItem = (id: string | undefined) => api<WorkItemDTO>(`/work/${id}`);

/** 작업 한 건의 라벨 제출 이력을 받는다. */
export const fetchWorkLabels = (id: string | undefined) => api<LabelDTO[]>(`/work/${id}/labels`);

/** 작업 한 건의 검수 이력을 받는다. */
export const fetchWorkReviews = (id: string | undefined) => api<ReviewDTO[]>(`/work/${id}/reviews`);

/** 라벨 초안을 임시저장한다. */
export const saveWorkDraft = (id: string | undefined, payload: unknown) =>
  api<WorkItemDTO>(`/work/${id}/draft`, { method: "PATCH", body: JSON.stringify({ payload }) });

/** 라벨을 제출한다. 서버도 제출 조건(캡션 최소 길이)을 다시 검사한다. */
export const submitWork = (id: string | undefined, payload: unknown) =>
  api<WorkItemDTO>(`/work/${id}/submit`, { method: "POST", body: JSON.stringify({ payload }) });

/** 비식별화 미흡을 신고한다. 서버가 보유를 풀고 그 클립의 작업을 막는다. */
export const reportDeidentMiss = (id: string | undefined, body: { timestamp_ms: number; reason: string }) =>
  api<void>(`/work/${id}/report-deident-miss`, { method: "POST", body: JSON.stringify(body) });

/**
 * 완료 목록 한 페이지를 받는다.
 *
 * 집계 없이 부른다(`summary=false`). 머리의 수는 `fetchCompletedSummary` 한 번으로 받는다.
 * 그래서 검색어를 입력할 때마다 전수 검증을 다시 돌리지 않는다.
 *
 * Args:
 *   pid: 프로젝트.
 *   limit: 한 페이지 행 수.
 *   offset: 앞에서 건너뛸 행 수.
 *   filter: 필터 축. `all`이면 싣지 않는다.
 *   query: 검색어. 비었으면 싣지 않는다.
 */
export const fetchCompletedPage = (pid: string | null, limit: number, offset: number, filter: string, query: string) =>
  api<CompletedPage>(
    `/projects/${pid}/completed?limit=${limit}&offset=${offset}&summary=false${filter !== "all" ? `&filter=${filter}` : ""}${query ? `&q=${encodeURIComponent(query)}` : ""}`,
  );

/** 완료 전수 집계를 받는다. 검색어와 관계없다. */
export const fetchCompletedSummary = (pid: string | null) =>
  api<CompletedSummary>(`/projects/${pid}/completed/summary`);
