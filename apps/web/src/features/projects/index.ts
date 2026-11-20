/**
 * 프로젝트 기능의 공개 진입점. 다른 곳은 이 파일로만 프로젝트 조회·부품·사전을 가져온다.
 *
 * 셸과 거의 모든 화면이 프로젝트 목록을 읽으므로 조회 훅과 키 팩토리를 이 파일에서 내보낸다.
 * 라우트(`routes.tsx`)는 내보내지 않는다. 셸이 이 파일을 읽고 라우트는 셸의 가드를 읽으므로 import가 순환한다.
 */
export {
  fetchAnalysisPresets,
  fetchProject,
  projectKeys,
  useProject,
  useProjects,
  type AnalysisPresetDTO,
} from "./api";
export { ConnectionNotice } from "./components/ConnectionNotice";
export { ProjectsPage } from "./pages/projects";
export { ProjectShellState } from "./components/ProjectShellState";
export { FIELD_META, type FormKey } from "./lib/settings_form";
export { PROJECT_STATUS_LABEL } from "./lib/project_status";
