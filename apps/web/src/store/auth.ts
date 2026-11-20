/** 로그인 사용자 상태(zustand). */
import { create } from "zustand";

import { fetchMe } from "@/features/auth";
import { type ApiError, type UserDTO } from "@/shared/api/client";
import { announceSignOut, clearSession, onSignedOutElsewhere, revokeSession } from "@/shared/api/auth";
import { queryClient } from "@/app/queryClient";

interface AuthState {
  user: UserDTO | null;
  loading: boolean;
  /**
   * `loadMe()`가 실패했을 때의 오류.
   * 아직 불러오지 않은 상태와 불러오지 못한 상태를 구분한다.
   * 오류를 버리면 셸은 null `user`만 보고 "아직 로딩 중"으로 읽어 `불러오는 중…`에서 멈춘다.
   */
  error: ApiError | null;
  loadMe: () => Promise<void>;
  signOut: () => void;
}

/** 로그인 사용자 스토어. `loadMe()`로 불러오고 `signOut()`으로 비운다. */
export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: false,
  error: null,
  async loadMe() {
    set({ loading: true, error: null });
    try {
      const me = await fetchMe();
      set({ user: me, loading: false, error: null });
    } catch (e) {
      // 401은 `api()`가 먼저 세션을 비우고 만료 모달을 띄우므로 이 처리까지 거의 오지 않는다.
      // 이 처리에 오는 것은 429·5xx·타임아웃 같은 일시 오류라서 사용자가 다시 시도할 수 있게 오류를 남긴다.
      set({ user: null, loading: false, error: e as ApiError });
    }
  },
  signOut() {
    // 서버에 세션 폐기를 먼저 요청하고 결과는 기다리지 않는다.
    // 네트워크 오류가 나도 로그아웃은 끝나야 한다.
    // revokeSession이 토큰을 읽어야 하므로 clearSession보다 먼저 부른다.
    void revokeSession();
    announceSignOut();
    clearSession("signed-out");
    dropSignedInState(set);
  },
}));

/**
 * 로그인 사용자 상태와 쿼리 캐시를 비운다. 이 탭의 로그아웃과 다른 탭의 로그아웃이 같이 쓴다.
 *
 * 이전 사용자의 프로젝트·역할·큐 캐시가 다음 로그인 계정에 잠깐 노출되지 않게 캐시도 비운다.
 * 사이드바 my_role은 ["projects"] 캐시에서 파생되므로 비우지 않으면 계정을 바꾼 뒤 이전 역할이 잠깐 보인다.
 */
function dropSignedInState(set: (s: Partial<AuthState>) => void): void {
  set({ user: null, error: null });
  queryClient.clear();
}

// 다른 탭에서 로그아웃하면 이 탭도 사용자 상태와 캐시를 비운다. 토큰은 `shared/api/auth`가 이미 비웠다.
onSignedOutElsewhere(() => dropSignedInState(useAuth.setState));
