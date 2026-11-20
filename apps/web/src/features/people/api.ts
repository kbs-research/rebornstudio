/**
 * 멤버(`/projects/{id}/members/*`)의 서버 호출과 쿼리 키.
 *
 * 멤버 화면과 사람 서랍이 부르는 요청은 모두 이 파일에서 만든다. 경로와 본문 모양이 화면마다 흩어지면
 * 서버 경로가 바뀔 때 고칠 곳을 모두 찾기 어렵다.
 */
import { api, type ProjectMemberDTO } from "@/shared/api/client";

/** 멤버 쿼리 키 팩토리. */
export const peopleKeys = {
  /** 프로젝트 멤버 목록. 프로젝트를 아직 찾지 못했으면 `pid`가 비어 있다. */
  members: (pid: string | undefined) => ["members", pid] as const,
};

/** 프로젝트 멤버 목록을 받는다. 서버가 페이지 없이 모두 준다. */
export function listMembers(pid: string) {
  return api<ProjectMemberDTO[]>(`/projects/${pid}/members`);
}

/** 있는 계정을 이메일로 초대한다. 이미 초대한 사람을 다시 초대할 때도 쓴다. */
export function inviteMember(pid: string, invite: { email: string | null | undefined; role: string }) {
  return api<ProjectMemberDTO>(`/projects/${pid}/members/invite`, {
    method: "POST",
    body: JSON.stringify(invite),
  });
}

/** 멤버의 프로젝트 역할을 바꾼다. */
export function updateMemberRole(pid: string, userId: string, role: string) {
  return api(`/projects/${pid}/members/${userId}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

/** 멤버를 프로젝트에서 제거한다. 마지막 관리자면 서버가 409로 거절한다. */
export function removeMember(pid: string, userId: string) {
  return api<void>(`/projects/${pid}/members/${userId}`, { method: "DELETE" });
}
