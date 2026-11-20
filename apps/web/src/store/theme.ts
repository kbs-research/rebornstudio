/**
 * 다크/라이트 테마 상태. localStorage에 저장하고 document.documentElement 속성과 맞춘다.
 *
 * 잘못된 테마가 잠깐 보이는 현상(flash-of-wrong-theme)을 막으려고 import 시점에 초기 테마를 바로 적용한다.
 * 저장한 값이 없으면 운영체제 설정(`prefers-color-scheme`)을 따른다. 사용자가 한 번 바꾸면 그 값을 저장해 계속 쓴다.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

type Theme = "light" | "dark";

interface ThemeState {
  theme: Theme;
  toggle: () => void;
}

/** 문서 루트의 data-theme 속성을 바꾼다. */
function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/** 운영체제가 어두운 화면을 고르면 `dark`를 돌려준다. 확인할 수 없으면 `light`다. */
function systemTheme(): Theme {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

// 모듈을 import하는 즉시(React 렌더 전) 저장된 테마를 적용한다. 저장한 값이 없으면 운영체제 설정을 쓴다.
const _stored: Theme = (() => {
  try {
    const raw = localStorage.getItem("reborn.theme");
    if (raw) {
      const parsed = JSON.parse(raw) as { state?: { theme?: Theme } };
      const t = parsed?.state?.theme;
      if (t === "light" || t === "dark") return t;
    }
  } catch {
    // 저장소를 읽지 못하면 운영체제 설정을 쓴다.
  }
  return systemTheme();
})();
applyTheme(_stored);

/** 앱 전역 테마 스토어. */
export const useTheme = create<ThemeState>()(
  persist(
    (set) => ({
      theme: _stored,
      toggle() {
        set((s) => {
          const next: Theme = s.theme === "light" ? "dark" : "light";
          applyTheme(next);
          return { theme: next };
        });
      },
    }),
    {
      name: "reborn.theme",
      onRehydrateStorage: () => (state) => {
        if (state) applyTheme(state.theme);
      },
    },
  ),
);
