/**
 * 세션을 자동으로 갱신한다. 활동 중이면 만료 전에 조용히 refresh하고, 활동이 없으면 만료시킨다.
 *
 * access 토큰은 탈취됐을 때의 노출을 줄이려고 짧게 둔다. 무제한 토큰은 권한이 큰 계정일수록 위험하다.
 * 대신 활동 중일 때만 백그라운드에서 갱신해, 일하는 사람이 만료 전마다 다시 로그인하지 않게 한다.
 *
 * 강한 활동 신호는 사용자가 시작한 변경 요청(GET이 아닌 요청)이다. `shared/api/client.ts`의 `api()`가 markActivity()로 기록한다.
 * 약한 활동 신호는 keydown·pointerdown·scroll·wheel·touchstart다. 읽기만 하는 구간을 보완하려고 이 훅의 리스너가 기록한다.
 * 마우스 이동은 커서 흔들림을 활동으로 오인하므로 뺀다.
 * 백그라운드 폴링(GET)도 뺀다. 그래야 아무도 보지 않는 탭이 세션을 유지하지 않는다.
 * 마지막 활동 후 IDLE_LIMIT_MS(auth.ts, 30분)가 지나면 갱신을 멈춰 세션이 만료되고, 다시 로그인해야 한다.
 *
 * ProtectedLayout에서 한 번만 마운트한다(로그인한 셸 전체에 적용된다).
 */
import { useEffect } from "react";

import { getSessionExpiryMs, isAuthed, isIdle, markActivity, refreshSession } from "@/shared/api/auth";

const CHECK_INTERVAL_MS = 20_000; // 갱신이 필요한지 점검하는 주기.
const REFRESH_AHEAD_MS = 2 * 60 * 1000; // 만료 2분 전이면 미리 갱신한다.
const ACTIVITY_THROTTLE_MS = 5_000; // 과호출을 막으려고 입력 이벤트는 5초에 한 번만 기록한다.

/** 활동 기반 세션 자동 갱신을 건다. ProtectedLayout에서 한 번 호출한다. */
export function useSessionKeepalive(): void {
  useEffect(() => {
    // 약한 신호: 마우스 이동을 뺀 실제 입력만 기록한다.
    // scroll은 버블링하지 않으므로 capture로 받는다.
    let lastMark = 0;
    const onInput = () => {
      const now = Date.now();
      if (now - lastMark >= ACTIVITY_THROTTLE_MS) {
        lastMark = now;
        markActivity();
      }
    };
    const events: (keyof WindowEventMap)[] = [
      "keydown",
      "pointerdown",
      "scroll",
      "wheel",
      "touchstart",
    ];
    for (const e of events) {
      window.addEventListener(e, onInput as EventListener, { passive: true, capture: true });
    }

    // 만료가 임박했고, 활동 중이고, 탭이 보이면 백그라운드에서 갱신한다.
    // 유휴 상태면 갱신하지 않으므로 세션이 그대로 만료된다.
    const tick = async () => {
      if (!isAuthed()) return;
      if (document.hidden) return; // 백그라운드 탭은 갱신하지 않는다.
      if (isIdle()) return; // 유휴 상태이면 그대로 만료되고, 재로그인 모달이 안내한다.
      const exp = getSessionExpiryMs();
      if (exp == null) return;
      if (exp - Date.now() < REFRESH_AHEAD_MS) await refreshSession();
    };
    const t = window.setInterval(() => void tick(), CHECK_INTERVAL_MS);

    return () => {
      for (const e of events) {
        window.removeEventListener(e, onInput as EventListener, {
          capture: true,
        } as EventListenerOptions);
      }
      window.clearInterval(t);
    };
  }, []);
}
