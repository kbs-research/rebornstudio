/**
 * 워크벤치 공통 영상 단축키 훅. 재생·초/프레임 이동·배속을 다룬다(라벨링·검수·완료 보기·클립 보기가 함께 쓴다).
 *
 * 누를 키와 도움말 문구는 `lib/shortcuts.ts`의 `PLAYBACK_SHORTCUTS` 한 곳에 있다.
 * 이 훅은 표에서 `match`가 맞는 줄의 동작을 실행한다. 표를 한 곳에 모은 이유는 `shortcuts.ts`의 모듈 주석에 있다.
 *
 * 입력 필드(input/textarea/select)에 포커스가 있거나 수식키(⌘/Ctrl/Alt)가 함께 눌리면 무시한다.
 * 그래서 화면의 수식키 명령(임시저장 ⌘S, 제출 ⌘Enter)과 충돌하지 않는다.
 * 포커스된 버튼의 Space도 그 버튼이 처리한다(`ownsActivationKey`).
 */
import { useEffect } from "react";
import type { RefObject } from "react";
import type { MediaPlayerInstance } from "@vidstack/react";

import { hit, legend, PLAYBACK_SHORTCUTS } from "./shortcuts";

/**
 * 이 요소가 Space·Enter를 직접 처리하면 true. 포커스된 버튼·링크·체크박스·탭 등이다.
 *
 * 전역 단축키가 그 키를 가로채 `preventDefault`하면 키보드로 버튼을 누를 수 없다.
 * 그러면 버튼에 포커스를 두고 Space를 누를 때 버튼 대신 영상이 토글된다.
 * 공용 훅(Space)과 라벨링 훅이 모두 이 함수로 판정한다.
 */
export function ownsActivationKey(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  const tag = el.tagName;
  if (tag === "BUTTON" || tag === "SUMMARY") return true;
  if (tag === "A" && el.hasAttribute("href")) return true;
  const role = el.getAttribute("role");
  return !!role && ACTIVATABLE_ROLES.has(role);
}

/**
 * 키보드 활성화를 스스로 처리하는 ARIA 역할.
 * 이 역할이 붙은 요소의 Space·Enter는 건드리지 않는다.
 */
const ACTIVATABLE_ROLES = new Set([
  "button", "link", "checkbox", "radio", "switch", "tab", "option", "menuitem",
]);

/**
 * 글자를 입력하는 중이면 true(입력란과 `contenteditable`).
 * 입력 중에는 단축키를 동작시키지 않는다.
 */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  const tag = el?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!el?.isContentEditable;
}

/** 열린 대화상자가 있으면 true. 그때 워크벤치 키는 처리하지 않고 그 창이 키를 받는다. */
function dialogOpen(): boolean {
  return !!document.querySelector('[role="dialog"]');
}

/** 1프레임 시간(초). 30fps로 가정한다. */
const FRAME = 1 / 30;
const RATE_MIN = 0.25;
const RATE_MAX = 2;
const RATE_STEP = 0.25;

export interface VideoShortcutOptions {
  /** false면 단축키를 끈다(모달 열림 등). 기본값은 true다. */
  enabled?: boolean;
  /** `?` 키를 누르면 부른다. 도움말을 열고 닫는다. */
  onToggleHelp?: () => void;
  /** 배속 상한. 기본값은 2배다. 클립 보기처럼 빠르게 훑어야 하는 화면만 올려 쓴다. */
  maxRate?: number;
  /**
   * 화면이 직접 처리하는 키이면 true. true인 키는 이 훅이 처리하지 않는다.
   * 화면이 쓰는 키를 재생 키로도 처리하면 한 번 눌렀을 때 두 동작이 모두 실행된다.
   */
  claim?: (e: KeyboardEvent) => boolean;
}

/** playerRef에 영상 트랜스포트 단축키를 연결한다. 키 표는 `PLAYBACK_SHORTCUTS`다. */
export function useVideoShortcuts(
  playerRef: RefObject<MediaPlayerInstance | null>,
  { enabled = true, onToggleHelp, maxRate = RATE_MAX, claim }: VideoShortcutOptions = {},
) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const typing = isTyping(e.target);
      // `?`는 도움말 창을 닫는 키이기도 해서 창이 떠 있어도 받는다.
      if (e.key === "?" && !typing) {
        e.preventDefault();
        onToggleHelp?.();
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      // 창이 떠 있으면 재생 키를 동작시키지 않는다.
      // 제출 확인 창 위에서 `]`로 영상이 움직이면 무엇을 확인하는지 알 수 없게 된다.
      if (dialogOpen()) return;
      // 포커스된 버튼의 Space는 그 버튼을 누르는 키라서 영상 토글로 처리하지 않는다.
      if (e.key === " " && ownsActivationKey(e.target)) return;
      if (claim?.(e)) return;
      const p = playerRef.current;
      if (!p) return;
      const seek = (d: number) => {
        p.currentTime = Math.max(0, p.currentTime + d);
      };
      const setRate = (r: number) => {
        p.playbackRate = Math.min(maxRate, Math.max(RATE_MIN, Number(r.toFixed(2))));
      };
      const is = (id: string) => hit(PLAYBACK_SHORTCUTS, id, e);
      // ←/→(`back10`)는 `match`가 없어 여기서 처리하지 않는다. `VideoPlayer`가 window 캡처로 처리한다.
      if (is("toggle")) {
        e.preventDefault();
        if (p.paused) void p.play();
        else p.pause();
      } else if (is("pause")) {
        e.preventDefault();
        p.pause();
      } else if (is("back5")) {
        e.preventDefault();
        seek(e.key.toLowerCase() === "j" ? -5 : 5);
      } else if (is("back1")) {
        e.preventDefault();
        seek(e.key === "[" ? -1 : 1);
      } else if (is("frame")) {
        e.preventDefault();
        p.pause();
        seek(e.key === "," ? -FRAME : FRAME);
      } else if (is("rate")) {
        e.preventDefault();
        setRate(p.playbackRate + (e.key === "-" ? -RATE_STEP : RATE_STEP));
      } else if (is("rate1")) {
        e.preventDefault();
        setRate(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playerRef, enabled, onToggleHelp, maxRate, claim]);
}

/** 공통 영상 단축키 레전드. 도움말 모달의 `재생` 그룹이다. 기준 표는 `PLAYBACK_SHORTCUTS`다. */
export const VIDEO_SHORTCUTS = legend(PLAYBACK_SHORTCUTS);
