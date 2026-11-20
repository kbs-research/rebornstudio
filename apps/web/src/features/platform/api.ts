/**
 * 플랫폼 관리(사용자·자격증명 연결·플랫폼 개요)의 서버 호출과 쿼리 키.
 *
 * 화면이 경로 문자열을 직접 쓰지 않게 한다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 주의: 쿼리 키의 배열 값은 다른 화면의 캐시·무효화와 같은 키다. 사이드바 배지·환영 창·감사 로그·
 * 프로젝트 생성과 설정이 같은 배열을 쓰므로 값을 바꾸면 무효화가 그 캐시에 적용되지 않는다.
 */
import {
  api,
  type ConnectionDTO,
  type ConnectionSaved,
  type DiagnosticsReport,
  type DomainEventDTO,
  type UserDTO,
} from "@/shared/api/client";
import type { components } from "@reborn/contracts";

import type { ConnectionHealth } from "./lib/connection_health";

/**
 * 발급된 재설정 토큰. 서버가 평문을 저장하지 않으므로 이 응답에서만 받을 수 있다.
 * `valid_minutes`도 서버가 준다. 화면이 `30분`이라고 직접 적으면 서버 값이 바뀔 때 어긋난다.
 */
export interface ResetIssued {
  user_id: string;
  email: string;
  token: string;
  expires_at: string;
  valid_minutes: number;
}

/**
 * 플랫폼 화면이 쓰는 쿼리 키 팩토리.
 *
 * 연결 키는 프로젝트 생성·설정 화면이 같이 쓴다.
 */
export const platformKeys = {
  /** 주기 점검이 남긴 마지막 연동 진단. */
  diagnosticsLast: () => ["admin-diagnostics-last"] as const,
  /** 자격증명 연결의 점검 상태 요약. */
  connectionsHealth: () => ["admin-connections-health"] as const,
  /** 전체 사용자 목록. */
  allUsers: () => ["admin-users-all"] as const,
  /** 한 사용자의 활동 이력. 창을 열기 전에는 `uid`가 비어 있다. */
  userActivity: (uid: string | undefined) => ["user-activity", uid] as const,
  /** 자격증명 연결 목록. */
  connections: () => ["connections"] as const,
};

/**
 * 주기 점검이 남긴 마지막 연동 진단 결과를 받는다. 외부를 호출하지 않는다.
 * 클라이언트 주소와 TLS는 서버가 이 요청으로 다시 판정해 싣는다.
 */
export function fetchLastDiagnostics(): Promise<DiagnosticsReport | null> {
  return api<DiagnosticsReport | null>("/admin/diagnostics/last");
}

/** 자격증명 연결의 마지막 점검 결과를 센 요약을 받는다. 외부를 호출하지 않는다. */
export function fetchConnectionsHealth(): Promise<ConnectionHealth> {
  return api<ConnectionHealth>("/admin/connections/health");
}

/** 전체 사용자 목록을 받는다(super_admin). */
export function fetchAllUsers(): Promise<UserDTO[]> {
  return api<UserDTO[]>("/users");
}

/** 계정 생성 결과. 만든 계정과 비밀번호를 정할 1회용 재설정 링크 토큰이다. */
export type UserCreated = components["schemas"]["UserCreated"];

/** 활성 계정을 만든다(super_admin). 비밀번호는 응답의 재설정 링크로 사용자가 정한다. */
export function createUser(body: { email: string; display_name: string; global_role: string }): Promise<UserCreated> {
  return api<UserCreated>("/admin/users", { method: "POST", body: JSON.stringify(body) });
}

/** 플랫폼 역할을 바꾼다. */
export function setUserRole(userId: string, role: string): Promise<UserDTO> {
  return api<UserDTO>(`/admin/users/${userId}/role`, { method: "PATCH", body: JSON.stringify({ role }) });
}

/** 사용자를 정지하거나 정지를 푼다. */
export function setUserStatus(userId: string, status: "active" | "suspended"): Promise<UserDTO> {
  return api<UserDTO>(`/admin/users/${userId}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
}

/** 비밀번호 재설정 링크의 토큰을 발급한다. */
export function issuePasswordReset(userId: string): Promise<ResetIssued> {
  return api<ResetIssued>(`/admin/users/${userId}/password-reset`, { method: "POST" });
}

/** 한 사용자의 활동 이력을 최근 것부터 `limit`건 받는다. */
export function fetchUserActivity(userId: string, limit: number): Promise<DomainEventDTO[]> {
  return api<DomainEventDTO[]>(`/admin/users/${userId}/activity?limit=${limit}`);
}

/** 자격증명 연결 목록을 받는다. 서버는 시크릿 값을 돌려주지 않는다. */
export function fetchConnections(): Promise<ConnectionDTO[]> {
  return api<ConnectionDTO[]>("/connections");
}

/** 자격증명 연결을 만든다. 서버가 저장한 뒤 바로 점검하고 결과(`check`)를 함께 돌려준다. */
export function createConnection(body: Record<string, unknown>): Promise<ConnectionSaved> {
  return api<ConnectionSaved>("/connections", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 자격증명 연결을 고친다. 시크릿은 본문에 있을 때만 바뀐다. 서버가 저장한 뒤 바로 점검한다. */
export function updateConnection(id: string, body: Record<string, unknown>): Promise<ConnectionSaved> {
  return api<ConnectionSaved>(`/connections/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

/** 자격증명 연결을 지운다. 지운 시크릿은 되살릴 수 없다. */
export function deleteConnection(id: string) {
  return api(`/connections/${id}`, { method: "DELETE" });
}
