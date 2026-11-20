/**
 * 라우트 단위 권한 가드(pathless layout route로 쓴다).
 *
 * router.tsx에서 보호 라우트 묶음을 감싸고, 권한 없이 접근하면 AccessDenied를 렌더한다.
 * 백엔드 가드에만 의존하면 빈 화면이나 원본 오류가 보인다.
 *
 * 역할 목록 대신 라우트 키(`route`)만 받고, 목록은 `lib/roles`의 `ROUTE_ACCESS`에서 읽는다.
 * 링크를 그리는 화면(`RoleLink`)과 사이드바가 같은 표를 읽으므로 열 수 없는 곳을 가리키지 않는다.
 *
 * - scope "global": user.global_role을 검사한다(예: super_admin 전용 admin/*).
 * - scope "project": 프로젝트의 my_role을 검사한다. slug 라우트면 :slug로, 아니면 선택된
 *   프로젝트로 찾는다. 고른 프로젝트가 없으면 그 역할이 있는 프로젝트가 하나라도 있는지 확인한다.
 *   super_admin은 항상 통과한다.
 */
import { TriangleAlert } from "lucide-react";
import { Outlet, useParams } from "react-router-dom";

import { AccessDenied } from "./AccessDenied";
import { Button } from "@/shared/ui/Button";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { StatePanel } from "@/shared/ui/StatePanel";
import { globalRoleLabel, projectRoleLabel } from "@/lib/role_label";
import {
  deniedCopy,
  hasGlobalRole,
  hasProjectRole,
  isSuperAdmin,
  ROUTE_ACCESS,
  type Destination,
  type RouteAccess,
  type RouteKey,
} from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/**
 * 가드가 판정을 내리기 전까지 보여 주는 골격을 그린다. 범위(scope)별로 모양이 다르다.
 *
 * 모든 보호 라우트가 이 로딩 화면을 거친다.
 * 한 줄짜리 글자를 그리면 로딩이 끝날 때 화면 전체가 흔들린다(`PageSkeleton` docstring과 같은 이유).
 * 가드는 `<Outlet/>` 전체를 대신하므로 페이지의 `PageHeader`도 아직 없어서 `header`를 켠다.
 *
 * 모양은 `router.tsx`의 목적지 구성에 맞춘다.
 * - `global`: 사용자·자격증명 연결·플랫폼 개요다. 대부분이 표 한 장이라 `table`을 쓴다.
 * - `project`: 통계·사람·보류·설정·파이프라인·영상 상세다. 표와 카드가 섞여 있어
 *   어느 한쪽을 따르면 나머지에서 화면이 두 번 흔들리므로, 공통 블록 골격인 `panels={2}`(본문·사이드 비대칭)를 쓴다.
 *
 * 예외로 `review/:id`는 전체 화면 워크벤치라 이 골격과 모양이 다르다.
 * 가드가 받는 키(`review`)는 목록 리다이렉트(`/review`)와 워크벤치를 함께 덮어 이 함수에서 구분할 수 없다.
 * 워크벤치는 진입 직후 자기 `WorkbenchSkeleton`으로 바뀐다.
 */
function Loading({ scope }: { scope: "global" | "project" }) {
  return scope === "global" ? <PageSkeleton header table /> : <PageSkeleton header panels={2} />;
}

/** 라우트 키의 권한을 검사한다. 통과하면 하위 라우트를, 막히면 접근 안내를 그린다. */
export function RoleGuard({ route }: { route: RouteKey }) {
  const access = ROUTE_ACCESS[route];
  const { user } = useAuth();
  const { slug } = useParams<{ slug?: string }>();
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects({ enabled: !!user && access.scope === "project" });

  // 인증 정보를 불러오는 중(_layout이 loadMe를 호출하고 있다).
  if (!user) return <Loading scope={access.scope} />;

  if (access.scope === "global") {
    if (hasGlobalRole(user, access.roles)) return <Outlet />;
    const copy = deniedCopy(access, globalRoleLabel);
    return <AccessDenied reason="global" message={copy.message} hint={copy.hint} />;
  }

  // project 범위: super_admin은 프로젝트 조회 없이 통과한다.
  if (isSuperAdmin(user)) return <Outlet />;
  if (projects.isLoading) return <Loading scope="project" />;
  // 목록을 받지 못한 경우는 일시 오류일 수 있으므로 "멤버가 아님"과 구분해 표시한다.
  if (projects.isError && !projects.data) {
    return (
      <StatePanel
        tone="bad"
        icon={<TriangleAlert size={18} aria-hidden="true" />}
        title="프로젝트 목록 조회 실패"
        lead="이 화면을 열 수 있는지는 프로젝트 목록을 받은 뒤에 확인됩니다."
        actions={<Button onClick={() => void projects.refetch()}>다시 시도</Button>}
      />
    );
  }

  const project = slug
    ? projects.data?.find((p) => p.slug === slug)
    : projects.data?.find((p) => p.project_id === selectedProjectId);

  /*
    주소에 프로젝트가 없는 화면(`/review/:id`)은 프로젝트를 고르지 않은 채 열 수 있다(새 브라우저, 받은 링크).
    그 역할을 가진 프로젝트가 하나라도 있으면 들여보낸다. 화면이 작업에서 프로젝트를 읽고,
    그 작업을 볼 수 있는지는 서버가 판정한다(없는 작업은 404, 다른 사람의 프로젝트는 403).
  */
  if (!project && !slug && projects.data?.some((p) => hasProjectRole(user, p, access.roles))) return <Outlet />;
  if (!project) return <AccessDenied reason={slug ? "not-member" : "no-project"} />;
  if (hasProjectRole(user, project, access.roles)) return <Outlet />;

  const copy = deniedCopy(access, projectRoleLabel);
  // 표가 `as const`라 `alt`를 가진 항목이 없으면 좁힌 타입이 `unknown`이 된다. 표의 선언 타입으로 읽는다.
  const alt = (access as RouteAccess & { alt?: Destination }).alt;
  return <AccessDenied reason="role" message={copy.message} hint={copy.hint} to={alt} project={project} />;
}
