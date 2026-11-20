/**
 * 제품 워드마크(호박색 R 표식, 제품 이름, 에디션 이름). 인증 화면·최초 설정·개인정보 안내가 같은 모양을 쓴다.
 *
 * 에디션 이름은 제품 이름 옆에 작게 둔다. 같은 제품의 다른 에디션과 화면만 보고 구분할 수 있게 한다.
 */
import { cn } from "@/shared/lib/cn";
import { EDITION_NAME, PRODUCT_NAME } from "@/shared/lib/edition";

/** 에디션 이름 표시. 사이드바 브랜드 줄도 같은 모양으로 쓴다. */
export function EditionLabel({ className }: { className?: string }) {
  return <span className={cn("text-micro font-semibold text-label-3", className)}>{EDITION_NAME}</span>;
}

/** 워드마크를 그린다. */
export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2.5 text-body font-bold text-label">
      <span className="grid size-7 place-items-center rounded-control bg-amber text-footnote font-bold text-on-amber">R</span>
      {PRODUCT_NAME}
      <EditionLabel />
    </span>
  );
}
