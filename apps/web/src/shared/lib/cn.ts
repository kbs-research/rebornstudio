/** Tailwind 클래스 합치기 도우미. */
import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * Tailwind 클래스 머지 함수(shadcn 컨벤션).
 *
 * 커스텀 타이포 토큰(text-display·title-1·title-2·headline·body·callout·footnote·caption·micro·nano)을
 * font-size 그룹으로 등록한다.
 * 등록하지 않으면 tailwind-merge가 이 토큰을 text-COLOR로 오인한다.
 * 그러면 text-accent-fg 같은 실제 색 유틸과 충돌한 것으로 보고 색 클래스를 버린다.
 * 예: primary 버튼 글자색이 사라져 상속색으로 표시된다(저대비).
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        { text: ["display", "title-1", "title-2", "headline", "body", "callout", "footnote", "caption", "micro", "nano"] },
      ],
    },
  },
});

/** 조건부 클래스를 합치고 Tailwind 클래스 충돌을 정리한다. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
