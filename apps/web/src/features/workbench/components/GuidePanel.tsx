import { useEffect, useRef } from "react";
import { BookOpen, X } from "lucide-react";
import type { components } from "@reborn/contracts";

import { CAPTION_LENGTH_UNIT } from "./label_parts";

type ProjectDTO = components["schemas"]["ProjectDTO"];

/**
 * 워크벤치 안의 작업 가이드 서랍. 워크벤치를 떠나지 않고 작업 가이드를 보게 한다.
 *
 * 배경막 없이 레일 위에 겹친다. 호출부는 머리 줄 아래 본문(`relative`) 안에 둔다.
 * 가이드를 보는 동안에도 영상이 가려지지 않고 재생 단축키가 동작해야 한다.
 * 워크벤치 단축키는 열린 대화상자(`role="dialog"`)가 있으면 멈추므로 이 서랍은 보조 영역(`complementary`)으로 둔다.
 *
 * 내용은 프로젝트 설정의 `guide_md`를 평문 그대로 보여 준다(개요 화면과 같다). 아래에 이 프로젝트의 기준
 * (캡션 최소 길이·반려 사유)을 설정 값으로 붙인다. 워크벤치가 이미 받은 프로젝트 응답을 쓴다.
 * 가이드가 비어 있으면 머리글만 둔다.
 */
export function GuidePanel({
  open,
  onClose,
  project,
}: {
  open: boolean;
  onClose: () => void;
  project: ProjectDTO | undefined;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  // 열면 닫기 버튼에 포커스를 둔다. 키보드로 연 사람이 Esc 외에 닫기 버튼도 바로 쓸 수 있다.
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector('[role="dialog"]')) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;

  const guide = project?.guide_md?.trim() ?? "";
  const reasons = (project?.reject_reasons ?? []).filter((r) => r.enabled);
  return (
    <aside
      aria-label="작업 가이드"
      className="absolute inset-y-0 right-0 z-20 flex w-full flex-col border-l border-separator bg-canvas-2 shadow-e3 lg:w-[416px]"
    >
      <div className="flex shrink-0 items-center gap-3 border-b border-separator px-6 py-4">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-capsule bg-fill text-label-2">
          <BookOpen size={16} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-headline font-bold text-label">작업 가이드</h2>
          {project?.name && <p className="truncate text-footnote text-label-3">{project.name}</p>}
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label="작업 가이드 닫기"
          className="inline-flex size-8 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label"
        >
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {guide ? (
          <p className="whitespace-pre-line text-body text-label">{guide}</p>
        ) : (
          <p className="text-body font-semibold text-label-2">작업 가이드 없음</p>
        )}
        {project && (
          <section className="mt-6 border-t border-separator pt-5">
            <h3 className="mb-3 text-caption font-semibold text-label-3">이 프로젝트의 기준</h3>
            <dl className="space-y-3 text-footnote">
              <div className="flex gap-4">
                <dt className="w-20 shrink-0 text-label-3">캡션 길이</dt>
                <dd className="min-w-0">
                  <span className="font-semibold tabular-nums text-label">최소 {project.caption_min_tokens}</span>
                  <span className="mt-0.5 block text-caption text-label-3">{CAPTION_LENGTH_UNIT}</span>
                </dd>
              </div>
              {/* 사유는 누를 수 없는 이름이라 버튼처럼 보이는 테두리 칩으로 그리지 않는다. 넷 이상이라 목록으로 쓴다. */}
              {reasons.length > 0 && (
                <div className="flex gap-4">
                  <dt className="w-20 shrink-0 text-label-3">반려 사유</dt>
                  <dd>
                    <ul className="space-y-1 text-label">
                      {reasons.map((r) => (
                        <li key={r.code} className="flex items-center gap-2">
                          <span className="size-1 shrink-0 rounded-capsule bg-label-3" aria-hidden="true" />
                          {r.label}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              )}
            </dl>
          </section>
        )}
      </div>
    </aside>
  );
}
