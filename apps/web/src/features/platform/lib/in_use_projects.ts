/**
 * 사용 중 거절(409 `connection_in_use`, `allowed_bucket_in_use`)을 막은 프로젝트의 안내 줄.
 *
 * 서버는 이름을 앞의 일부만 싣고 나머지는 수로 준다. 나머지 수를 빼면 보이는 프로젝트만 바꾸면 되는 것처럼
 * 읽혀 틀린 안내가 되므로 수를 함께 적는다.
 */
import type { InUseProjects } from "@/shared/api/client";

/** 막는 프로젝트를 `사용 중인 프로젝트: A, B 외 N개` 꼴의 한 줄로 만든다. */
export function inUseProjectsLine(p: InUseProjects): string {
  const more = p.more > 0 ? ` 외 ${p.more.toLocaleString("ko-KR")}개` : "";
  return `사용 중인 프로젝트: ${p.names.join(", ")}${more}`;
}
