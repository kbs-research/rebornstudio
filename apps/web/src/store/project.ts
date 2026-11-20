/** 선택한 프로젝트 상태(zustand). */
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface State {
  selectedProjectId: string | null;
  setSelectedProjectId: (id: string | null) => void;
}

/**
 * 프로젝트 전환기를 여는 신호. 셸 밖의 화면(프로젝트 선택이 필요한 빈 상태)이 전환기를 연다.
 *
 * 전환기의 열림 상태는 사이드바 안에 있어 화면이 직접 바꿀 수 없다. 화면은 `requestProjectSwitcher`를
 * 부르고, 전환기(`shell/ProjectSwitcher`)가 `nonce`가 바뀔 때마다 팝오버를 연다.
 * 열림을 불리언으로 두면 이미 참일 때 두 번째 요청이 값을 바꾸지 못해 닫힌 전환기가 다시 열리지 않으므로 증가하는 수로 둔다.
 * 새로고침 뒤에 저절로 열리지 않도록 저장하지 않는다.
 */
export const useProjectSwitcherRequest = create<{ nonce: number; request: () => void }>()((set) => ({
  nonce: 0,
  request: () => set((s) => ({ nonce: s.nonce + 1 })),
}));

/** 프로젝트 전환기를 연다. 사이드바가 없는 화면(플랫폼 화면 등)에서는 아무 일도 없다. */
export function requestProjectSwitcher(): void {
  useProjectSwitcherRequest.getState().request();
}

/** 선택한 프로젝트 id. localStorage(`reborn.project`)에 저장해 새로고침 뒤에도 유지한다. */
export const useSelectedProject = create<State>()(
  persist(
    (set) => ({
      selectedProjectId: null,
      setSelectedProjectId: (id) => set({ selectedProjectId: id }),
    }),
    { name: "reborn.project" },
  ),
);
