/**
 * 오른쪽에서 밀려 나오는 패널. 현재 화면을 유지한 채 상세·보조 내용을 보여 준다.
 * Esc 키나 백드롭 클릭으로 닫는다.
 *
 * 제목이 이 대화상자의 접근성 이름이 된다.
 * `role="dialog"`는 내용에서 이름을 얻지 못해서, `aria-label`이나 `aria-labelledby`가 없으면
 * 열렸을 때 "대화 상자"라고만 읽힌다.
 * 그래서 화면에 이미 있는 제목을 `h2`로 렌더하고 그 id를 `aria-labelledby`로 가리킨다.
 * 제목을 넘기지 않는 호출부에서는 연결하지 않는다. 없는 id를 가리키면 이름이 오히려 사라진다.
 *
 * 액션 줄(`footer`)은 패널이 바닥에 고정해서 그린다.
 * 액션이 본문 끝에 붙으면 긴 드로어에서 `보내기`를 누르려고 본문을 끝까지 스크롤해야 한다.
 * 본문만 스크롤하는 구조(`min-h-0 flex-1 overflow-y-auto`) 아래에 고정 줄 하나를 둔다.
 *
 * 제목 문자열 안에 아이콘을 넣으면 `truncate`가 아이콘까지 자르므로 `icon` 슬롯을 따로 둔다.
 */
import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { useDialogFocus } from "@/shared/hooks/useDialogFocus";

import { cn } from "@/shared/lib/cn";

/** 아이콘 배지의 색조. `Modal`과 같은 값을 쓴다. */
type DrawerTone = "neutral" | "accent" | "warn" | "danger";

const TONE: Record<DrawerTone, string> = {
  neutral: "bg-canvas-2 text-label-2",
  accent: "bg-accent-muted text-accent",
  warn: "bg-warn-muted text-warn-text",
  danger: "bg-bad-muted text-bad-text",
};

/** 오른쪽 슬라이드 패널을 그린다. 열려 있는 동안 포커스를 패널 안에 가둔다. */
export function Drawer({
  open,
  onClose,
  title,
  icon,
  tone,
  footer,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** 제목 왼쪽 아이콘. `tone`을 같이 주면 원형 배지로 감싼다. */
  icon?: ReactNode;
  tone?: DrawerTone;
  /** 바닥에 고정되는 액션 줄. 본문이 길어도 바닥에 남는다. */
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  // 포커스를 패널 안에 가둔다. 이유는 `Modal`과 같다(`shared/hooks/useDialogFocus`).
  useDialogFocus(open, panelRef);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[50]"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title != null ? titleId : undefined}
    >
      <div className="absolute inset-0 animate-fade-in bg-backdrop" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          "absolute inset-y-0 right-0 flex w-full max-w-3xl animate-slide-in-right flex-col bg-panel shadow-e3",
          "focus:outline-none",
          className,
        )}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-separator px-6 py-4">
          {icon != null &&
            (tone ? (
              <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-capsule", TONE[tone])}>
                {icon}
              </span>
            ) : (
              <span className="flex shrink-0 items-center text-label-3">{icon}</span>
            ))}
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-headline font-bold text-label">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex size-8 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label-2"
            aria-label="닫기"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && (
          <div className="flex shrink-0 items-center justify-end gap-2 border-t border-separator px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
