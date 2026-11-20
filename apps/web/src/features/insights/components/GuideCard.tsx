/**
 * 개요의 작업 가이드 카드. 관리자가 쓴 글이라 역할마다 펼침 정도가 다르다.
 *
 * 라벨러·검수자에게는 계속 참고하는 판정 기준이라 펼친 채 읽기 좋은 폭과 줄 간격으로 둔다.
 * 관리자에게는 세 줄만 보이고 `전체 보기 · N줄`로 편다. 편집은 설정의 작업 가이드 페이지로 간다.
 * 이름은 설정 화면과 같은 `작업 가이드`다.
 */
import { BookOpen, ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import { CardHead, MoreLink } from "./ManagerCards";
import { Card } from "@/shared/ui/Card";
import { cn } from "@/shared/lib/cn";

/** 관리자 개요에서 처음 보이는 줄 수 */
const PEEK_LINES = 3;

/**
 * 가이드 원문을 줄 목록으로 나눈다. 빈 줄은 뺀다.
 * 원문의 번호·글머리는 관리자가 쓴 그대로 둔다. 화면이 번호를 다시 매기면 두 번 붙는다.
 */
function linesOf(text: string): string[] {
  return text.split("\n").map((l) => l.trimEnd()).filter((l) => l.trim() !== "");
}

/** 작업 가이드 본문이 있으면 참을 돌려준다. 없으면 카드가 한 줄짜리 `작업 가이드 없음`이 되어 개요 배치가 달라진다. */
export function hasGuideText(text: string | null | undefined): boolean {
  return linesOf(text ?? "").length > 0;
}

/**
 * 작업 가이드 카드를 그린다.
 *
 * Args:
 *   mode: `read`(펼침, 작업자), `peek`(세 줄, 관리자).
 *   editTo: 편집할 곳. 설정을 여는 역할에게만 준다.
 */
export function GuideCard({
  text,
  mode,
  editTo,
}: {
  text: string | null | undefined;
  mode: "read" | "peek";
  editTo?: string;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const lines = linesOf(text ?? "");

  if (lines.length === 0) {
    return (
      <Card as="section" className="flex items-center gap-2 px-5 py-4">
        <BookOpen className="size-4 shrink-0 text-label-3" aria-hidden="true" />
        <h2 className="text-body font-semibold text-label">작업 가이드 없음</h2>
        {editTo && <span className="ml-auto"><MoreLink to={editTo}>설정에서 작성</MoreLink></span>}
      </Card>
    );
  }

  const peek = mode === "peek" && !open;
  const shown = peek ? lines.slice(0, PEEK_LINES) : lines;
  return (
    <Card as="section" aria-labelledby={`${bodyId}-title`}>
      <CardHead
        id={`${bodyId}-title`}
        title="작업 가이드"
        right={editTo ? <MoreLink to={editTo}>설정에서 편집</MoreLink> : undefined}
      />
      <div
        id={bodyId}
        className={cn(
          "space-y-1.5 whitespace-pre-wrap px-5 pb-4 text-body text-label",
          mode === "read" && "max-w-[680px] leading-relaxed",
        )}
      >
        {shown.map((l, i) => <p key={i}>{l}</p>)}
      </div>
      {mode === "peek" && lines.length > PEEK_LINES && (
        <div className="px-5 pb-4">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 rounded-mark text-footnote font-semibold text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            {open ? "접기" : `전체 보기 · ${lines.length}줄`}
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          </button>
        </div>
      )}
    </Card>
  );
}
