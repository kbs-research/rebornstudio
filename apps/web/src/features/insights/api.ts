/**
 * 통계·개요의 서버 호출과 쿼리 키.
 *
 * 같은 조회를 개요 카드, 통계의 사람별 실적, 사람 서랍, 멤버 표가 나눠 쓴다. 키를 한 곳에서 만들어야 카드가 여럿이어도
 * 요청이 한 번이고, 한 화면의 무효화가 다른 화면의 캐시에도 적용된다.
 * 주의: 키 배열의 값은 바꾸지 않는다. 사람 설정 저장 뒤의 무효화가 `["labeler-perf", pid]` 접두사로 찾는다.
 */
import type { components } from "@reborn/contracts";

import {
  api,
  type BusinessKpi,
  type LabelerPerfRow,
  type LabelerPerfSummary,
  type WorkflowHealth,
} from "@/shared/api/client";

/** 장기 대기·납품 불가 사유 응답. */
export type CapacityHealth = components["schemas"]["CapacityHealth"];
/** 통계·개요 쿼리 키 팩토리. 프로젝트를 아직 찾지 못했으면 `pid`가 비어 있다. */
export const insightsKeys = {
  /** 진척률과 납품 가능 수. 개요 카드 여럿이 같은 키로 한 번만 부른다. */
  businessKpi: (pid: string | undefined) => ["business-kpi", pid] as const,
  /** 상태별 작업 수. */
  workflowHealth: (pid: string | undefined) => ["workflow-health", pid] as const,
  /** 장기 대기와 납품 불가 사유. */
  capacity: (pid: string | undefined) => ["capacity", pid] as const,
  /** 사람별 실적 전체의 접두사. 사람 설정을 저장하면 이 접두사로 모든 기간의 캐시를 무효화한다. */
  labelerPerfAll: (pid: string | undefined) => ["labeler-perf", pid] as const,
  /** 사람별 실적. 기간을 넣지 않으면 기간을 바꿔도 캐시된 이전 수가 그대로 보인다. */
  labelerPerf: (pid: string | undefined, period: string) => ["labeler-perf", pid, period] as const,
  /** 기간 안의 프로젝트 합계. 사람별 실적 접두사 아래에 두어 사람별 실적을 무효화하면 합계도 함께 다시 받는다. */
  labelerSummary: (pid: string | undefined, period: string) => ["labeler-perf", pid, period, "summary"] as const,
};

/** 진척률과 납품 가능 수를 받는다. 진척 판정은 서버가 한다. */
export const fetchBusinessKpi = (pid: string) =>
  api<BusinessKpi>(`/projects/${pid}/dashboard/business-kpi`);

/** 상태별 작업 수를 받는다. */
export const fetchWorkflowHealth = (pid: string) =>
  api<WorkflowHealth>(`/projects/${pid}/dashboard/workflow-health`);

/** 장기 대기와 납품 불가 사유를 받는다. */
export const fetchCapacity = (pid: string) => api<CapacityHealth>(`/projects/${pid}/dashboard/capacity`);

/** 사람별 실적을 받는다. `since`가 없으면 전체 기간이다. */
export const fetchLabelerPerformance = (pid: string, since?: string | null) =>
  api<LabelerPerfRow[]>(`/projects/${pid}/dashboard/labeler-performance${since ? `?since=${encodeURIComponent(since)}` : ""}`);

/** 기간 안의 프로젝트 합계를 받는다. 같은 `since`의 사람별 실적 행을 서버가 더한 값이다. */
export const fetchLabelerSummary = (pid: string, since?: string | null) =>
  api<LabelerPerfSummary>(
    `/projects/${pid}/dashboard/labeler-performance/summary${since ? `?since=${encodeURIComponent(since)}` : ""}`,
  );
