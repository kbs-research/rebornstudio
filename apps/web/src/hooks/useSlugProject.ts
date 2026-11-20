/**
 * URL의 `/p/:slug`를 실제 선택 프로젝트와 맞춘다.
 *
 * 이 훅이 없으면 프로젝트는 스위처가 고른 `selectedProjectId`(localStorage)로만 정해진다.
 * 그러면 `/p/news-2024/...` 링크를 열어도 받는 사람이 마지막에 고른 프로젝트가 열리고,
 * "이 작업 봐주세요" 링크가 다른 프로젝트를 연다.
 *
 * slug가 접근 가능한 프로젝트를 가리키면 그 프로젝트로 전환한다(URL이 우선한다).
 * 목록에 없는 slug면 `unknown`을 돌려주고, 호출자가 "그런 프로젝트가 없습니다"를 보여 준다.
 * 다른 프로젝트를 대신 보여 주면 사용자는 맞는 프로젝트를 보고 있다고 착각한다.
 * slug가 없는 경로(`/my/...` 등)에서는 아무것도 하지 않는다.
 *
 * 주소에 프로젝트가 없는 개인 화면은 아래 `useProjectParam`(`?project=`)과
 * `useSyncProjectContext`(연 작업이 속한 프로젝트)가 맡는다.
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/** 경로에서 `/p/{slug}`의 slug만 뽑는다. 라우트 계층과 무관하게 동작한다. */
export function slugFromPath(pathname: string): string | null {
  const m = /^\/p\/([^/]+)/.exec(pathname);
  return m ? decodeURIComponent(m[1]) : null;
}

/**
 * `?project=` 값을 읽는다. 주소 경로에 프로젝트가 없는 화면(`/my/*`)이 쓰는 힌트다.
 *
 * 경로에 `/p/{slug}`가 있으면 경로가 우선한다. 사람은 주소창의 `/p/a`를 읽고 질의 문자열은 읽지 않으므로,
 * 둘이 다르면 눈에 보이는 경로를 따른다.
 */
export function projectParamFromUrl(pathname: string, search: string): string | null {
  if (slugFromPath(pathname)) return null;
  const q = new URLSearchParams(search).get("project")?.trim();
  return q ? q : null;
}

/**
 * 주소의 프로젝트와 고른 프로젝트를 맞춘 상태.
 *
 * - `none`: 주소에 `/p/{slug}`가 없다.
 * - `syncing`: 목록을 받는 중이거나, 고른 프로젝트를 주소의 프로젝트로 바꾸는 중이다.
 * - `ok`: 고른 프로젝트가 주소의 프로젝트와 같다.
 * - `unknown`: 목록에 그 slug가 없다.
 * - `error`: 목록을 받지 못했다. `retry`로 다시 부른다.
 */
export type SlugState = "none" | "syncing" | "ok" | "unknown" | "error";

/** URL의 slug로 선택 프로젝트를 전환하고, 맞춤 상태와 목록 재시도 함수를 돌려준다. */
export function useSlugProject(): { slug: string | null; state: SlugState; retry: () => void } {
  const { pathname } = useLocation();
  const slug = slugFromPath(pathname);
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();

  const projects = useProjects({ enabled: !!slug, staleTime: 60_000 });

  const target = projects.data?.find((p) => p.slug === slug);

  useEffect(() => {
    if (!slug || !target) return;
    if (target.project_id !== selectedProjectId) setSelectedProjectId(target.project_id);
  }, [slug, target, selectedProjectId, setSelectedProjectId]);

  const retry = () => void projects.refetch();
  if (!slug) return { slug: null, state: "none", retry };
  // 받지 못한 목록은 `error`로 돌려준다. 받는 중(`syncing`)으로 두면 셸이 본문을 계속 미뤄 화면이 멈춘다.
  if (!projects.data) return { slug, state: projects.isError ? "error" : "syncing", retry };
  if (!target) return { slug, state: "unknown", retry };
  return { slug, state: target.project_id === selectedProjectId ? "ok" : "syncing", retry };
}

/** 셸 본문 위치에 그릴 것. */
export type LayoutBody = "auth-error" | "project-error" | "unknown-project" | "hold" | "page";

/**
 * 셸 본문 위치에 무엇을 그릴지 정한다. `app/_layout.tsx`가 부른다.
 *
 * 주소의 프로젝트로 맞추는 동안(`syncing`)에는 본문을 그리지 않고 골격을 세운다.
 * 본문을 그리면 화면들이 고른 프로젝트(지난번 값)의 id로 먼저 조회해서, 404 토스트
 * (`영상을 찾을 수 없습니다`)가 뜨거나 다른 프로젝트의 목록을 먼저 받는다.
 * 이 slug의 본문을 이미 그렸으면(`shownSlug`) 내리지 않는다.
 * 전환기로 다른 프로젝트를 고른 직후 이동이 막히면(저장하지 않은 편집) 주소와 선택이 잠깐 어긋난다.
 * 그때 본문을 내리면 편집이 사라진다. `shownSlug`는 `nextShownSlug`로 갱신한다.
 */
export function layoutBody(p: {
  authFailed: boolean;
  slug: string | null;
  state: SlugState;
  shownSlug: string | null;
}): LayoutBody {
  if (p.authFailed) return "auth-error";
  if (p.state === "error") return "project-error";
  if (p.state === "unknown") return "unknown-project";
  if (p.state === "syncing" && p.shownSlug !== p.slug) return "hold";
  return "page";
}

/**
 * 본문을 그린 slug를 갱신한다. 셸이 렌더 뒤마다 부른다.
 *
 * 맞춘 상태(`ok`)가 되면 그 slug를 기억하고, 주소가 프로젝트 밖으로 나가면 지운다.
 * 지우지 않으면 개인 화면에서 고른 프로젝트가 바뀐 뒤(`?project=` 링크) 같은 slug로 돌아왔을 때
 * 본문을 미루지 않아 바뀐 프로젝트로 먼저 조회한다.
 */
export function nextShownSlug(prev: string | null, slug: string | null, state: SlugState): string | null {
  if (!slug) return null;
  return state === "ok" ? slug : prev;
}

/**
 * 연 작업이 속한 프로젝트로 전환기를 맞춘다.
 *
 * 워크벤치 주소(`/work/{id}/label`·`/review/{id}`)에는 프로젝트가 없다.
 * 이 훅은 링크에 값을 더 싣지 않고 연 작업의 프로젝트를 그대로 쓴다.
 * 그래서 북마크로 열든 주소를 직접 입력하든, 서버가 보낸 링크 모양과 관계없이 같은 값이 나온다.
 * 맞추지 않으면 반려 알림으로 B 프로젝트 작업을 연 뒤 헤더의 `내 작업`을 누를 때
 * 전환기가 여전히 A라서 A의 큐와 지표가 뜬다.
 *
 * 작업의 프로젝트가 아직 `undefined`이면 아무것도 하지 않는다.
 * 그 상태에서 값을 비우면 화면이 한 번 깜빡이며 "프로젝트 미선택"을 거친다.
 */
export function useSyncProjectContext(projectId: string | undefined): void {
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();
  useEffect(() => {
    if (!projectId || projectId === selectedProjectId) return;
    setSelectedProjectId(projectId);
  }, [projectId, selectedProjectId, setSelectedProjectId]);
}

/**
 * `?project={slug}`를 전환기에 반영한다.
 *
 * 개인 작업공간(`/my/*`)은 주소에 프로젝트가 없어서 전환기 값(localStorage)만 따른다.
 * 그러면 B 프로젝트를 가리키는 링크를 열어도 A(마지막 선택)의 목록이 열린다.
 * 경로를 `/p/:slug/my/...`로 옮기면 북마크의 이전 주소가 깨지므로, 링크가 프로젝트를 싣고 이 훅이 그 값을 전환기에 옮긴다.
 *
 * 프로젝트를 찾지 못하면 아무것도 바꾸지 않는다. 경로 slug와 달리 이 값은 힌트다.
 * 오래된 링크 하나 때문에 화면 전체를 "프로젝트 없음"으로 덮으면 볼 수 있는 자기 작업까지 가려진다.
 * 보고 있는 프로젝트는 화면의 프로젝트 칩이 표시하므로 칩과 이 링크를 함께 쓴다.
 */
export function useProjectParam(): void {
  const { pathname, search } = useLocation();
  const want = projectParamFromUrl(pathname, search);
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();

  const projects = useProjects({ enabled: !!want, staleTime: 60_000 });
  const target = projects.data?.find((p) => p.slug === want);

  useEffect(() => {
    if (!target || target.project_id === selectedProjectId) return;
    setSelectedProjectId(target.project_id);
  }, [target, selectedProjectId, setSelectedProjectId]);
}
