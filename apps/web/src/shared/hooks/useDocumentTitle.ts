/**
 * 브라우저 탭 제목(`document.title`)을 지금 화면의 이름으로 정한다.
 *
 * 형식은 `{화면} · {프로젝트} · RebornStudio Essential`이고, 프로젝트 밖 화면은 가운데 조각을 뺀다.
 * 제목을 쓰는 곳이 둘이면 효과가 도는 순서에 따라 마지막 제목이 바뀐다.
 * 그래서 화면은 이름을 올리기만 하고(`useDocumentTitle`), 셸의 상단바가 한 곳에서 제목을 쓴다(`useDocumentTitleWriter`).
 * 올라온 이름이 없으면 상단바가 브레드크럼의 끝 마디를 쓴다. 상세 화면의 끝 마디도 올라온 이름을 쓴다.
 * 셸 밖 화면(로그인 등)에는 상단바가 없으므로 올린 이름을 이 훅이 바로 쓴다.
 * 탭 제목이 모두 같으면 여러 탭을 구분할 수 없고, 스크린리더 사용자는 화면이 바뀐 것을 알 수 없다
 * (WCAG 2.4.2).
 */
import { useEffect } from "react";
import { create } from "zustand";

import { PRODUCT_FULL_NAME } from "@/shared/lib/edition";

/** 탭 제목 끝에 붙는 앱 이름(제품 이름과 에디션 이름). `index.html`의 기본 제목과 같다. */
export const APP_NAME = PRODUCT_FULL_NAME;

/** 탭 제목을 만든다. 빈 조각은 뺀다. */
export function formatDocumentTitle(screen?: string | null, project?: string | null): string {
  return [screen, project, APP_NAME]
    .map((part) => part?.trim())
    .filter((part): part is string => !!part)
    .join(" · ");
}

/** 화면이 올린 이름 하나. 같은 이름을 두 화면이 올려도 따로 내리도록 번호를 붙인다. */
interface Claim {
  id: number;
  title: string;
}

/** 화면이 올린 이름 목록을 담는 저장소. 가장 나중에 올린 이름을 쓴다. */
const usePageTitleStore = create<{ claims: Claim[] }>(() => ({ claims: [] }));
let nextClaimId = 1;
/** 탭 제목을 쓰는 셸 상단바가 마운트된 수. 0이면 올린 이름을 바로 쓴다. */
let mountedWriters = 0;

/**
 * 화면 이름을 올리고, 내리는 함수를 돌려준다. 훅 밖에서 쓰거나 테스트할 때 부른다.
 *
 * 여럿이 올라와 있으면 가장 나중에 올린 이름을 쓴다.
 */
export function claimPageTitle(title: string): () => void {
  const id = nextClaimId++;
  usePageTitleStore.setState((s) => ({ claims: [...s.claims, { id, title }] }));
  return () => usePageTitleStore.setState((s) => ({ claims: s.claims.filter((c) => c.id !== id) }));
}

/** 지금 올라와 있는 화면 이름을 돌려준다. 없으면 `null`. */
export function currentPageTitle(): string | null {
  const { claims } = usePageTitleStore.getState();
  return claims.length ? claims[claims.length - 1].title : null;
}

/**
 * 화면 이름을 올린다. 마운트된 동안 탭 제목과 상세 화면 브레드크럼의 끝 마디가 이 이름을 쓴다.
 *
 * `PageHeader`가 제목으로 부른다. 전체 화면 워크벤치처럼 `PageHeader`가 없는 화면은 직접 부른다.
 * 빈 값이면 아무것도 올리지 않으므로 데이터를 받기 전에는 브레드크럼의 이름을 쓴다.
 */
export function useDocumentTitle(screen: string | null | undefined): void {
  useEffect(() => {
    const title = screen?.trim();
    if (!title) return;
    const release = claimPageTitle(title);
    if (mountedWriters === 0) document.title = formatDocumentTitle(title);
    return release;
  }, [screen]);
}

/** 지금 올라와 있는 화면 이름을 구독한다. 상단바의 브레드크럼과 탭 제목이 쓴다. */
export function usePageTitle(): string | null {
  return usePageTitleStore((s) => (s.claims.length ? s.claims[s.claims.length - 1].title : null));
}

/**
 * 탭 제목을 쓴다. 셸의 상단바가 한 번 부른다.
 *
 * 올라온 화면 이름이 있으면 그 이름을, 없으면 `fallback`(브레드크럼 끝 마디)을 쓴다.
 * `project`는 주소에 프로젝트가 있는 화면에서만 넘긴다.
 */
export function useDocumentTitleWriter(fallback: string | null, project: string | null): void {
  const claimed = usePageTitle();
  useEffect(() => {
    mountedWriters += 1;
    return () => {
      mountedWriters -= 1;
    };
  }, []);
  const title = formatDocumentTitle(claimed ?? fallback, project);
  useEffect(() => {
    document.title = title;
  }, [title]);
}
