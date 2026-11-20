/**
 * 개인 작업공간이 지금 보고 있는 프로젝트를 보여 주는 칩.
 *
 * `내 작업`·`내 활동`은 주소 대신 왼쪽 위 전환기 값을 따른다.
 * 화면 제목은 `내 작업공간`뿐이라, 칩이 없으면 다른 프로젝트를 가리키는 링크(`?project=`)로 들어왔을 때
 * 맥락이 바뀐 것을 본문 어디서도 알 수 없다.
 *
 * 이름은 `/projects`가 준 `name`을 그대로 쓰고 slug로 만들지 않는다.
 * 빈 이름이나 이전 이름을 잠깐이라도 보여 주면 사용자는 그 순간 본 것을 맞다고 믿으므로, 목록이 오기 전에는 칩을 그리지 않는다.
 *
 * 고른 프로젝트가 없거나 목록에 없는 id가 남아 있으면 `프로젝트 미선택`으로 표시한다.
 * 이때 첫 프로젝트를 임의로 골라 보여 주면 선택하지 않은 프로젝트를 선택된 것처럼 표시하게 된다.
 *
 * 누르면 그 프로젝트의 개요로 가고, 선택된 프로젝트가 없으면 프로젝트 목록으로 간다.
 * 같은 일을 하는 컨트롤이 두 개면 어느 쪽이 기준인지 알 수 없으므로 프로젝트 전환은 전환기 한 곳에서만 한다.
 *
 * 프로젝트에 따라 내용이 바뀌는 화면에서만 그린다. 대상 목록은 `nav-config`의 `PERSONAL_PROJECT_SCOPED` 한 곳에 있다.
 */
import { Link, useLocation } from "react-router-dom";
import { FolderOpen } from "lucide-react";

import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

import { PERSONAL_PROJECT_SCOPED } from "./nav-config";

const CHIP =
  "inline-flex max-w-full items-center gap-1.5 h-5.5 rounded-capsule border border-separator-strong bg-panel px-2 text-caption font-semibold text-label-2 transition-colors hover:bg-canvas-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

/** 선택된 프로젝트 칩을 그린다. 프로젝트 기준 화면이 아니거나 목록을 아직 받지 못했으면 그리지 않는다. */
export function PersonalProjectChip() {
  const { pathname } = useLocation();
  const scoped = PERSONAL_PROJECT_SCOPED.includes(pathname);
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects({ staleTime: 60_000, enabled: scoped });

  if (!scoped) return null;
  // 목록을 받지 못했으면 이름이 없으므로 칩을 그리지 않는다.
  if (!projects.data) return null;

  const current = projects.data.find((p) => p.project_id === selectedProjectId);
  if (!current) {
    return (
      <Link to="/projects" className={CHIP} title="프로젝트 목록으로 이동">
        <FolderOpen size={12} className="shrink-0 text-label-3" aria-hidden="true" />
        <span className="text-label-3">프로젝트 미선택</span>
      </Link>
    );
  }
  return (
    <Link
      to={`/p/${current.slug}/overview`}
      className={CHIP}
      title={`${current.name} 개요로 이동`}
    >
      <FolderOpen size={12} className="shrink-0 text-label-3" aria-hidden="true" />
      <span className="truncate">{current.name}</span>
    </Link>
  );
}
