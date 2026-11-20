/**
 * 프로젝트 도메인의 서버 호출·쿼리 키·조회 훅.
 *
 * 프로젝트 목록은 셸과 거의 모든 화면이 읽는다. 호출과 키를 이 파일 한 곳에 두어
 * 생성·저장·삭제 뒤의 무효화가 모든 화면과 같은 키를 가리키게 한다.
 * 주의: 키의 배열 값은 다른 화면의 캐시·무효화와 같은 값이다. 값을 바꾸면 무효화가 그 캐시에 적용되지 않는다.
 */
import { useQuery } from "@tanstack/react-query";

import {
  api,
  type DiagnosticsReport,
  type ProjectDTO,
  type ProjectStats,
} from "@/shared/api/client";
import type { components } from "@reborn/contracts";

/** 분석 스택 카탈로그의 한 항목. */
export type AnalysisPresetDTO = components["schemas"]["AnalysisPresetDTO"];
/** 프로젝트 생성 요청 본문. 계약에서 생성된 타입이다. */
export type ProjectCreateRequest = components["schemas"]["ProjectCreateRequest"];
/** 프로젝트 설정 저장 본문. 계약에서 생성된 타입이다. 서버는 모르는 필드를 422로 거절한다. */
export type ProjectUpdateRequest = components["schemas"]["ProjectUpdateRequest"];
/** 설정에서 고를 수 있는 연결 하나. 이름·프로바이더·시크릿 유무·마지막 점검 결과만 있다. */
export type ConnectionChoiceDTO = components["schemas"]["ConnectionChoiceDTO"];
/** 연결 선택지의 목적. 품질검수 모델 연결(`caption_qc`)만 있다. */
export type ConnectionPurpose = "caption_qc";
/** 프로젝트가 고른 분석 대상(MI 연결·워크스페이스·MI 프로젝트). 저장 요청과 응답이 같은 모양이다. */
export type ProjectAnalysisStackDTO = components["schemas"]["ProjectAnalysisStackDTO"];
/** 분석 대상 화면의 현재 선택·잠금·기본값·선택지. */
export type AnalysisChoicesDTO = components["schemas"]["AnalysisChoicesDTO"];
export type ProjectStorageStackDTO = components["schemas"]["ProjectStorageStackDTO"];
export type StorageChoicesDTO = components["schemas"]["StorageChoicesDTO"];

/** 프로젝트 도메인의 쿼리 키 팩토리. */
export const projectKeys = {
  /** 현재 사용자가 볼 수 있는 프로젝트 목록. super_admin에게는 전체 프로젝트다. */
  list: () => ["projects"] as const,
  /** 프로젝트 한 건. 라벨링·검수 워크벤치가 같은 캐시를 쓴다. */
  detail: (projectId: string | null | undefined) => ["project", projectId] as const,
  /** 분석 스택 카탈로그. 프로젝트마다 다르지 않아 키에 프로젝트가 없다. */
  analysisPresets: () => ["analysis-presets"] as const,
  /** 분석 연동 진단. */
  analysisProvisioning: (projectId: string) => ["analysis-provisioning", projectId] as const,
  /** 분석 대상 선택지. 고른 연결·워크스페이스마다 목록이 달라 키에 같이 넣는다. */
  analysisChoices: (projectId: string, connectionId?: string | null, workspace?: string | null) =>
    ["analysis-choices", projectId, connectionId ?? "", workspace ?? ""] as const,
  /** 분석 대상 선택지 전체. 저장 뒤 무효화에 쓴다. */
  analysisChoicesAll: (projectId: string) => ["analysis-choices", projectId] as const,
  /** 저장소 대상 선택지. 고른 연결마다 버킷 목록이 달라 키에 같이 넣는다. */
  storageChoices: (projectId: string, connectionId?: string | null) =>
    ["storage-choices", projectId, connectionId ?? ""] as const,
  /** 저장소 대상 선택지 전체. 저장 뒤 무효화에 쓴다. */
  storageChoicesAll: (projectId: string) => ["storage-choices", projectId] as const,
  /** 프로젝트 설정에서 고를 수 있는 연결(등록한 연결 이름). 목적을 주면 그 목적에 쓸 수 있는 연결만 담는다. */
  connectionChoices: (projectId: string | null | undefined, purpose?: ConnectionPurpose) =>
    ["connection-choices", projectId, purpose ?? ""] as const,
  /** 영상·클립·작업·멤버 수. 프로젝트 삭제 확인 창이 영향 범위로 쓴다. */
  stats: (projectId: string) => ["project-stats", projectId] as const,
};

/** 프로젝트 목록을 받는다. 항목마다 현재 사용자의 역할(`my_role`)이 실린다. */
export function fetchProjects(): Promise<ProjectDTO[]> {
  return api<ProjectDTO[]>("/projects");
}

/** 프로젝트 한 건을 받는다. */
export function fetchProject(projectId: string): Promise<ProjectDTO> {
  return api<ProjectDTO>(`/projects/${projectId}`);
}

/** 프로젝트를 만든다(super_admin). */
export function createProject(body: ProjectCreateRequest): Promise<ProjectDTO> {
  return api<ProjectDTO>("/projects", { method: "POST", body: JSON.stringify(body) });
}

/** 프로젝트 설정 중 바뀐 필드만 보낸다. */
export function updateProject(projectId: string, body: ProjectUpdateRequest): Promise<ProjectDTO> {
  return api<ProjectDTO>(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** 프로젝트의 영상·클립·작업·멤버 수를 받는다(관리자·검수자). */
export function fetchProjectStats(projectId: string): Promise<ProjectStats> {
  return api<ProjectStats>(`/projects/${projectId}/stats`);
}

/** 프로젝트를 지운다. 서버는 `deleted_at`만 남기는 soft delete다. */
export function deleteProject(projectId: string): Promise<void> {
  return api<void>(`/projects/${projectId}`, { method: "DELETE" });
}

/**
 * 프로젝트 설정에서 고를 수 있는 연결 이름을 받는다(프로젝트 관리자).
 * 연결 관리 목록(`/connections`)은 최고관리자 전용이라 설정 화면은 이 조회를 쓴다.
 * 목적을 주면 서버가 그 목적에 쓸 수 있는 연결만 준다. 품질검수 모델 연결을 화면이 다시 거르지 않는다.
 */
export function fetchConnectionChoices(projectId: string, purpose?: ConnectionPurpose): Promise<ConnectionChoiceDTO[]> {
  return api<ConnectionChoiceDTO[]>(`/projects/${projectId}/connection-choices${purpose ? `?purpose=${purpose}` : ""}`);
}

/** 분석 스택 카탈로그를 받는다. 쓸 수 없는 스택도 이유와 함께 온다. */
export function fetchAnalysisPresets(): Promise<AnalysisPresetDTO[]> {
  return api<AnalysisPresetDTO[]>("/projects/analysis-presets");
}

/** 분석 연동 상태를 진단한다. 읽기만 한다. */
export function fetchAnalysisProvisioning(projectId: string): Promise<DiagnosticsReport> {
  return api<DiagnosticsReport>(`/projects/${projectId}/analysis-provisioning`);
}

/**
 * 분석 대상의 현재 선택과 선택지를 받는다. 연결을 주면 그 연결로 워크스페이스 목록을 읽고, 비우면 읽지 않는다.
 * 워크스페이스를 주면 그 워크스페이스의 연동 저장소와 MI 프로젝트 목록도 온다.
 */
export function fetchAnalysisChoices(
  projectId: string,
  connectionId?: string | null,
  workspace?: string | null,
): Promise<AnalysisChoicesDTO> {
  const q = new URLSearchParams();
  if (connectionId) q.set("connection_id", connectionId);
  if (workspace) q.set("workspace", workspace);
  const qs = q.toString();
  return api<AnalysisChoicesDTO>(`/projects/${projectId}/analysis-choices${qs ? `?${qs}` : ""}`);
}

/** 분석 대상을 저장한다. 서버가 실제로 점검한 뒤 저장한다. */
export function saveAnalysisStack(projectId: string, body: ProjectAnalysisStackDTO): Promise<ProjectAnalysisStackDTO> {
  return api<ProjectAnalysisStackDTO>(`/projects/${projectId}/analysis-stack`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** 분석 대상을 지금 점검한다. 영상 등록·분석 요청 직전 점검의 캐시도 새로 쓴다. */
export function checkAnalysisStack(projectId: string): Promise<DiagnosticsReport> {
  return api<DiagnosticsReport>(`/projects/${projectId}/analysis-stack/check`, { method: "POST" });
}

/** 저장소 대상의 현재 선택과 선택지를 받는다. 연결을 주면 그 연결로 버킷 목록을 읽고, 비우면 읽지 않는다. */
export function fetchStorageChoices(projectId: string, connectionId?: string | null): Promise<StorageChoicesDTO> {
  const qs = connectionId ? new URLSearchParams({ connection_id: connectionId }).toString() : "";
  return api<StorageChoicesDTO>(`/projects/${projectId}/storage-choices${qs ? `?${qs}` : ""}`);
}

/** 저장소 대상을 저장한다. 서버가 버킷 권한을 실제로 점검한 뒤 저장한다. */
export function saveStorageStack(projectId: string, body: ProjectStorageStackDTO): Promise<ProjectStorageStackDTO> {
  return api<ProjectStorageStackDTO>(`/projects/${projectId}/storage-stack`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** 저장소 대상을 지금 점검한다. 영상 등록 직전 점검의 캐시도 새로 쓴다. */
export function checkStorageStack(projectId: string): Promise<DiagnosticsReport> {
  return api<DiagnosticsReport>(`/projects/${projectId}/storage-stack/check`, { method: "POST" });
}

/** 프로젝트 목록을 다시 받는 주기(ms). 역할 변경이 열린 탭에 닿는 최대 지연이다. */
const PROJECT_LIST_REFRESH_MS = 60_000;

/**
 * 프로젝트 목록 조회 옵션. 부르는 곳마다 필요한 값만 넘긴다.
 *
 * 넘기지 않은 옵션은 쿼리 클라이언트 기본값을 따른다. `undefined`를 명시해 넘기면 기본값을 덮으므로
 * 옵션 객체에는 쓰는 키만 둔다.
 */
export interface ProjectListOptions {
  enabled?: boolean;
  staleTime?: number;
}

/** 프로젝트 목록을 조회한다. 셸과 화면이 모두 이 훅으로 같은 캐시(`projectKeys.list()`)를 읽는다. */
export function useProjects(options: ProjectListOptions = {}) {
  return useQuery({
    queryKey: projectKeys.list(),
    queryFn: fetchProjects,
    /*
      이 목록의 `my_role`이 화면 권한(사이드바·첫 화면·가드)의 출처다. 다른 관리자가 현재 사용자의 역할을 바꾸면
      열린 탭에도 새로고침 없이 반영되어야 하므로 창으로 돌아올 때와 1분마다 다시 받는다.
      창이 가려져 있으면 받지 않는다.
    */
    refetchOnWindowFocus: true,
    refetchInterval: PROJECT_LIST_REFRESH_MS,
    ...options,
  });
}

/** 프로젝트 한 건을 조회한다. id가 없는 동안에는 요청하지 않는다. */
export function useProject(projectId: string | null | undefined) {
  return useQuery({
    queryKey: projectKeys.detail(projectId),
    queryFn: () => fetchProject(projectId!),
    enabled: !!projectId,
  });
}
