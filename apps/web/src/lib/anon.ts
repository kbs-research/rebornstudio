/**
 * 익명화 유틸. 본인이 아닌 사람의 이름·PII를 결정적 해시 ID로 표시한다
 * (개인정보 보호).
 *
 * 운영·작업 화면에서 작업 인력끼리는 실명 대신 안정적 핸들(예 `WK·3F9A`)과 지표로 다룬다.
 * 관리자(project_admin·super_admin)는 운영·조치를 위해 실명을 본다.
 * 시스템·역할 표기("시스템", "관리자")는 개인 식별자가 아니므로 그대로 쓴다.
 */

import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/** seed를 결정적 해시 `PREFIX·XXXX`(base36 4자리)로 바꾼다. 같은 seed는 항상 같은 핸들이 된다. */
export function anonId(seed: string | null | undefined, prefix = "ID"): string {
  let h = 0;
  const s = String(seed ?? "");
  for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
  const code = h.toString(36).toUpperCase().slice(0, 4).padStart(4, "0");
  return `${prefix}·${code}`;
}

/** 표시할 이름을 고른다. 보는 사람이 관리자면 실명, 아니면 익명 ID이고, self=true면 "나"다. */
export function who(
  name: string | null | undefined,
  viewerIsAdmin: boolean,
  opts?: { prefix?: string; self?: boolean },
): string {
  if (opts?.self) return "나";
  if (viewerIsAdmin) return name ?? "—";
  return anonId(name, opts?.prefix ?? "ID");
}

/**
 * 현재 사용자가 관리자 권한이면 true를 돌려준다. super_admin(전역) 또는 선택 프로젝트의 project_admin이다.
 * `["projects"]` 캐시를 재사용한다. 화면은 `who(name, useViewerIsAdmin())`로 분기한다.
 */
export function useViewerIsAdmin(): boolean {
  const user = useAuth((s) => s.user);
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects({ enabled: !!user });
  if (user?.global_role === "super_admin") return true;
  return projects.data?.find((p) => p.project_id === selectedProjectId)?.my_role === "project_admin";
}
