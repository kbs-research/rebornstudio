/**
 * 작업 단계(단계 화면·검수·품질검수·완료·내보내기)의 서버 호출과 쿼리 키.
 *
 * 화면과 부품은 경로 문자열을 직접 두지 않고 이 파일의 함수를 부른다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 라벨링·검수 두 대기열의 보유 목록·대기 수·다음 작업은 개인 작업공간(`features/me`)의 호출과 `meKeys`를 쓴다.
 * 요청 주소는 동작마다 전체를 적는다. 화면이 서버 라우트를 부르는지 세는 게이트(`scripts/ci/check-orphan-routes.mjs`)는
 * 조립한 주소를 읽지 못한다.
 * 주의: 키 값은 다른 화면의 캐시·무효화와 같은 배열이다. 값을 바꾸면 오류 없이 무효화가 다른 키를 가리켜 그 화면이 갱신되지 않는다.
 */
import type { components } from "@reborn/contracts";

import {
  api,
  type LabelDTO,
  type PipelineSummary,
  type ReviewDTO,
} from "@/shared/api/client";

import type { CompletedFilter } from "./lib/completed_filter";

/** 보유 작업 한 건. */
type WorkItemDTO = components["schemas"]["WorkItemDTO"];

/**
 * 완료 목록 한 페이지.
 *
 * 계약 타입을 그대로 쓴다. 손으로 적은 타입은 서버가 필드를 더해도 화면에 반영되지 않고, 드리프트 게이트도 잡아내지 못한다.
 * 교차 타입의 두 필드는 생성물(`CompletedPage`)에도 있어 생성물과 겹친다.
 */
export type CompletedPage = components["schemas"]["CompletedPage"] & {
  filter_scanned?: number | null;
  filter_candidates?: number | null;
};

/** 완료 목록 전체 집계. 계약 타입을 그대로 쓴다. */
export type CompletedSummary = components["schemas"]["CompletedSummary"];

/**
 * 완료 클립 한 건의 상세. 개정 이력과 판정 이력에 사람 이름이 함께 온다.
 *
 * 계약 타입을 쓴다. 이름 필드 두 개는 생성물(`CompletedRevision`·`CompletedReview`)에도 있어 교차 부분은 겹친다.
 */
export type CompletedDetail = components["schemas"]["CompletedDetail"] & {
  revisions: (components["schemas"]["CompletedRevision"] & { submitted_by_name?: string | null })[];
  reviews: (components["schemas"]["CompletedReview"] & { reviewer_name?: string | null })[];
};

/**
 * 내보내기 한 건.
 *
 * 계약 타입을 그대로 쓴다. 교차 타입의 네 필드는 생성물(`ExportDTO`)에도 있어 교차 부분은 겹친다.
 */
export type ExportDTO = components["schemas"]["ExportDTO"] & {
  excluded_count?: number | null;
  attempts?: number;
  max_attempts?: number;
  filename?: string | null;
};
/** 내보내기 다운로드 링크. 만료되므로 누를 때 받는다. */
export type ExportDownloadResponse = components["schemas"]["ExportDownloadResponse"];

/** 반려가 여러 번 쌓인 미완 작업 목록. */
export type RepeatedRejectPage = components["schemas"]["RepeatedRejectPage"];
/** 반려 누적 작업 한 건. */
export type RepeatedRejectRow = components["schemas"]["RepeatedRejectRow"];

/** 작업 한 건의 품질검수 상태(`running`·`failed`·`recorded`·`none`)와 최신 실행. 상태는 서버가 정한다. */
export type WorkQc = components["schemas"]["WorkQcDTO"];
/** 품질검수 보류 목록 한 페이지. */
export type QcHeldPageDTO = components["schemas"]["QcHeldPage"];
/** 보류 해제·다시 검사의 결과. */
export type QcHoldResultDTO = components["schemas"]["QcHoldActionResult"];

/** 비식별화 신고 목록. */
export type DeidentReportPage = components["schemas"]["DeidentReportPage"];
/** 비식별화 신고 한 건. */
export type DeidentReportRow = components["schemas"]["DeidentReportRow"];
/** 비식별화 신고 처리 결과. */
export type DeidentReportResolveResult = components["schemas"]["DeidentReportResolveResult"];

/**
 * 작업 단계의 쿼리 키 팩토리.
 *
 * `…All`은 프로젝트나 조건을 뺀 앞부분이라 무효화에 쓴다.
 * 작업 한 건의 키(`work…`)는 라벨링 워크벤치가 같은 배열로 읽어 캐시를 나눠 쓴다.
 */
export const pipelineKeys = {
  summaryAll: () => ["pipeline-summary"] as const,
  /** 단계별 대기 건수. 스텝퍼·사이드바 배지·개요 카드가 같은 캐시를 읽는다. */
  summary: (pid: string | null | undefined) => ["pipeline-summary", pid] as const,
  /** 완료 목록. 거르기 축마다 따로 캐시한다. */
  completed: (pid: string | null, filter: CompletedFilter) => ["completed", pid, filter] as const,
  /** 완료 목록 전체 집계. 완료 워크벤치와 같은 캐시를 쓴다. */
  completedSummary: (pid: string | null | undefined) => ["completed-summary", pid] as const,
  /** 완료 클립 한 건의 상세. 상세 화면과 본문 부품이 같은 캐시를 쓴다. */
  completedDetail: (pid: string | null | undefined, workItemId: string | null | undefined) =>
    ["completed-detail", pid, workItemId] as const,
  /** 내보내기 이력. */
  exports: (pid: string) => ["exports", pid] as const,
  /** 반려 누적 작업 목록. */
  repeatedRejects: (pid: string | null) => ["repeated-rejects", pid] as const,
  /** 비식별화 신고 목록. */
  deidentReports: (pid: string | null) => ["deident-reports", pid] as const,
  /** 검수 대기열 키 전부의 앞부분(`["review", "mine", …]`). 판정·신고 뒤에 무효화한다. */
  reviewAll: () => ["review"] as const,
  /** 작업 한 건에 딸린 키 전부와 라벨링 보유 목록의 앞부분. 판정·신고 뒤에 무효화한다. */
  workAll: () => ["work"] as const,
  /** 작업 한 건. */
  workItem: (id: string | undefined) => ["work", "one", id] as const,
  /** 작업 한 건의 판정 이력. */
  workReviews: (id: string | undefined) => ["work", "reviews", id] as const,
  /** 작업 한 건의 제출 라벨 이력. */
  workLabels: (id: string | undefined) => ["work", "labels", id] as const,
  /** 작업 한 건의 캡션 품질검수 결과. */
  workQc: (id: string | undefined) => ["work", "qc", id] as const,
  /** 품질검수 보류 목록. */
  qcHeld: (pid: string | null | undefined) => ["qc-held", pid] as const,
  /** 품질검수 보류 전체 수. 개요의 조치 필요 카드가 수만 읽는다. 목록(`qcHeld`)과 캐시를 따로 쓴다. */
  qcHeldTotal: (pid: string) => ["qc-held-total", pid] as const,
};

// ─── 단계 화면 ────────────────────────────────────────────────────────────────

/** 단계별 대기 건수를 받는다. 영상 기준 단계(변환·분석)까지 서버가 한 번에 센다. */
export const fetchPipelineSummary = (pid: string) => api<PipelineSummary>(`/projects/${pid}/pipeline/summary`);

// ─── 완료·내보내기 ────────────────────────────────────────────────────────────

/** 완료 목록 한 페이지를 받는다. 거르기(`filter`)는 서버가 전체 목록에 건다. 집계는 따로 받으므로 `summary=false`다. */
export const fetchCompleted = (pid: string | null, { limit, offset, filter }: { limit: number; offset: number; filter: CompletedFilter }) =>
  api<CompletedPage>(
    `/projects/${pid}/completed?limit=${limit}&offset=${offset}`
    + `&summary=false&filter=${filter}`,
  );

/** 완료 목록 전체 집계를 받는다. */
export const fetchCompletedSummary = (pid: string | null) =>
  api<CompletedSummary>(`/projects/${pid}/completed/summary`);

/** 완료 클립 한 건의 상세를 받는다. */
export const fetchCompletedDetail = (pid: string | null, workItemId: string | null | undefined) =>
  api<CompletedDetail>(`/projects/${pid}/completed/${workItemId}`);

/** 내보내기 이력을 받는다. */
export const fetchExports = (pid: string) => api<ExportDTO[]>(`/projects/${pid}/exports`);

/** 완료 클립 전체를 담는 내보내기를 추가한다. */
export const createExport = (pid: string) =>
  api<ExportDTO>(`/projects/${pid}/exports`, { method: "POST" });

/** 내보내기 한 건의 다운로드 링크를 받는다. */
export const fetchExportDownload = (exportId: string) =>
  api<ExportDownloadResponse>(`/exports/${exportId}/download`);

// ─── 반려 누적 ───────────────────────────────────────────────────────────────

/** 반려 누적 작업 목록을 받는다. `limit`을 서버 조회 상한과 같게 두어야 상한 도달 판정이 맞는다. */
export const fetchRepeatedRejects = (pid: string | null, limit: number) =>
  api<RepeatedRejectPage>(`/projects/${pid}/work/repeated-rejects?limit=${limit}`);

// ─── 검수 ────────────────────────────────────────────────────────────────────

/** 작업 한 건을 받는다. */
export const fetchWorkItem = (id: string | undefined) => api<WorkItemDTO>(`/work/${id}`);

/** 작업 한 건의 판정 이력을 받는다. */
export const fetchWorkReviews = (id: string | undefined) => api<ReviewDTO[]>(`/work/${id}/reviews`);

/** 작업 한 건의 제출 라벨 이력을 받는다. 마지막 항목이 최신 제출이다. */
export const fetchWorkLabels = (id: string | undefined) => api<LabelDTO[]>(`/work/${id}/labels`);

/** 작업 한 건의 캡션 품질검수 결과를 받는다. 실행된 적이 없으면 `null`이다. */
export const fetchWorkQc = (id: string | undefined) => api<WorkQc>(`/work/${id}/qc`);

/** 검수 판정을 제출한다. 서버가 판정 초안을 비운다. */
export const decideReview = (id: string | undefined, body: object) =>
  api<WorkItemDTO>(`/work/${id}/review`, { method: "POST", body: JSON.stringify(body) });

/** 검수 초안을 저장한다. 다른 건으로 옮긴 뒤에도 이전 건에 저장되도록 작업 id를 요청마다 받는다. */
export const saveReviewDraft = (workItemId: string, body: object) =>
  api<WorkItemDTO>(`/work/${workItemId}/review/draft`, { method: "PATCH", body: JSON.stringify(body) });

/** 비식별화 미흡을 신고한다. 서버가 검수 점유를 풀고 판정 초안을 지운다. */
export const reportDeidentMiss = (id: string | undefined, body: { timestamp_ms: number; reason: string }) =>
  api<void>(`/work/${id}/report-deident-miss`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// ─── 비식별화 신고 ───────────────────────────────────────────────────────────

/** 비식별화 신고로 멈춘 클립 목록을 받는다. `limit`을 서버 조회 상한과 같게 두어야 상한 도달 판정이 맞는다. */
export const fetchDeidentReports = (pid: string | null, limit: number) =>
  api<DeidentReportPage>(`/projects/${pid}/deident-reports?limit=${limit}`);

/** 비식별화 신고를 처리한다. 재개는 작업을 대기열로 되돌리고, 제외는 클립을 납품에서 뺀다. */
export const resolveDeidentReport = (clipId: string, body: { action: "resume" | "exclude"; note?: string }) =>
  api<DeidentReportResolveResult>(`/clips/${clipId}/deident-report/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// ─── 캡션 품질검수 ────────────────────────────────────────────────────────────

/** 품질검수 보류 목록 한 페이지를 받는다. */
export const fetchQcHeld = (pid: string | null | undefined, { limit, offset }: { limit: number; offset: number }) =>
  api<QcHeldPageDTO>(`/projects/${pid}/qc/held?limit=${limit}&offset=${offset}`);

/** 품질검수 보류 전체 수를 받는다. `total`은 페이지와 관계없는 전체 수라서 한 건만 받는다. */
export const fetchQcHeldTotal = (pid: string) => api<{ total: number }>(`/projects/${pid}/qc/held?limit=1&offset=0`);

/** 품질검수 보류를 해제한다. 사유는 필수다. */
export const releaseQcHold = (pid: string | null | undefined, workItemId: string, reason: string) =>
  api<QcHoldResultDTO>(`/projects/${pid}/qc/${workItemId}/release`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

/**
 * 보류된 승인본의 캡션을 고쳐 다시 승인한다. 고친 문장만 보낸다(id와 본문).
 * 서버가 게이트를 `pending`으로 돌리고, 승인 검사가 다시 돌아 보류 여부를 정한다.
 */
export const reviseQcHold = (
  pid: string | null | undefined,
  workItemId: string,
  sentences: { id: string; text: string }[],
) =>
  api<QcHoldResultDTO>(`/projects/${pid}/qc/${workItemId}/revise`, {
    method: "POST",
    body: JSON.stringify({ sentences }),
  });

/** 보류한 작업을 다시 검사한다. 결과는 통과이거나 다시 보류다. */
export const recheckQcHold = (pid: string | null | undefined, workItemId: string) =>
  api<QcHoldResultDTO>(`/projects/${pid}/qc/${workItemId}/recheck`, {
    method: "POST",
  });
