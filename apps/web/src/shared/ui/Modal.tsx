/** 화면 가운데 뜨는 확인·입력 창(Modal). */
import { HTMLAttributes, ReactNode, useEffect, useId, useRef } from "react";

import { useDialogFocus } from "@/shared/hooks/useDialogFocus";

import { cn } from "@/shared/lib/cn";

/** 창 폭. sm 400px(한 줄 확인), md 520px(기본), lg 720px(표·긴 폼). */
type ModalSize = "sm" | "md" | "lg";
/** 아이콘 배지의 색조. 파괴적 확인창은 여기서 경고 색을 얻는다. */
type ModalTone = "neutral" | "accent" | "warn" | "danger";

const SIZE: Record<ModalSize, string> = {
  sm: "max-w-[400px]",
  md: "max-w-[520px]",
  lg: "max-w-[720px]",
};

const TONE: Record<ModalTone, string> = {
  neutral: "bg-canvas-2 text-label-2",
  accent: "bg-accent-muted text-accent",
  warn: "bg-warn-muted text-warn-text",
  danger: "bg-bad-muted text-bad-text",
};

interface Props extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** 헤더 제목 왼쪽 아이콘. `tone`을 같이 주면 원형 배지로 감싼다. */
  icon?: ReactNode;
  /**
   * 아이콘 배지의 색조. `icon`이 있을 때만 적용된다.
   * 파괴적 확인창은 `danger`, 되돌릴 수 있는 경고는 `warn`을 쓴다.
   */
  tone?: ModalTone;
  /** 창 폭. 기본값은 `md`(520px). */
  size?: ModalSize;
  /**
   * 액션 줄. 주면 구분선 아래에 고정되고 본문만 스크롤한다.
   *
   * pending 문구와 disabled 조건이 창마다 다르고 그 일은 래퍼(`WorkbenchConfirm`)가 맡으므로,
   * 버튼을 만들어 주는 prop(`confirmLabel`·`onConfirm`)은 두지 않는다. 이 컴포넌트는 위치와 여백만 맡는다.
   */
  footer?: ReactNode;
  /**
   * Esc와 배경 클릭으로 닫히면 true. 기본은 true.
   * 닫는 일이 되돌릴 수 없는 동작과 같은 창(세션 만료)은 끄고, 푸터의 버튼으로만 고르게 한다.
   */
  dismissible?: boolean;
  children: ReactNode;
}

/**
 * 화면 가운데 뜨는 확인·입력 창을 그린다. Esc 키나 백드롭 클릭으로 닫는다.
 *
 * 제목이 이 대화상자의 접근성 이름이 된다. `Drawer`와 같은 이유로
 * `role="dialog"`는 내용에서 이름을 얻지 못하므로, 제목을 `h2`로 렌더하고 `aria-labelledby`로 가리킨다.
 * 제목 없이 여는 호출부(확인 문구만 있는 창)에서는 연결하지 않는다.
 *
 * 액션 줄(`footer`)은 창이 그린다. 그러면 여백과 버튼 크기가 한 값으로 통일되고, 긴 창에서도 액션이 바닥에 남는다.
 * 호출부마다 버튼 줄을 직접 만들면 성격이 같은 창이 서로 다른 여백과 버튼 높이를 쓰게 된다.
 */
export function Modal({
  open, onClose, title, icon, tone, size = "md", footer, dismissible = true, children, className, ...rest
}: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  // 포커스를 창 안에 가둔다. `aria-modal="true"`가 선언한 동작을 DOM에서도 지킨다.
  useDialogFocus(open, panelRef);

  useEffect(() => {
    if (!open || !dismissible) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, dismissible]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title != null ? titleId : undefined}
      /*
        `items-start`와 패널의 `my-auto`를 함께 써서 높이가 큰 모달이 화면 위쪽에서 잘리지 않게 한다.
        짧은 모달은 가운데에 오고(`my-auto`), 큰 모달만 위에서 시작해 끝까지 스크롤된다(`overflow-y-auto`).
        `items-center`만 쓰면 뷰포트보다 높은 패널이 위아래로 똑같이 넘치고, 위로 넘친 부분은 스크롤로도 볼 수 없다.
        그러면 노트북 화면 높이(1280×640)에서 긴 폼 창의 제목 줄이 잘린다.
      */
      className="fixed inset-0 z-50 flex animate-fade-in items-start justify-center overflow-y-auto bg-backdrop p-4"
      onClick={dismissible ? onClose : undefined}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          "my-auto w-full animate-pop-in overflow-hidden rounded-overlay border border-separator bg-raised shadow-e3",
          "focus:outline-none",
          SIZE[size],
          /* 푸터가 있으면 창이 뷰포트를 넘지 않고 본문만 스크롤한다. */
          footer && "flex max-h-[calc(100vh-2rem)] flex-col",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
        {...rest}
      >
        {(title || icon) && (
          <div className="flex shrink-0 items-center gap-2.5 px-6 pb-1 pt-5.5">
            {/* 색조를 주면 원형 배지로 감싼다. 파괴적 확인창은 제목 줄의 색을 이 배지에서만 얻는다.
                맨 글리프와 배지가 섞이면 `gap-2.5 py-3` 헤더에서 제목 왼쪽 정렬이 어긋난다. */}
            {icon != null &&
              (tone ? (
                <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-capsule", TONE[tone])}>
                  {icon}
                </span>
              ) : (
                icon
              ))}
            {title && (
              <h2 id={titleId} className="text-headline font-bold text-label">
                {title}
              </h2>
            )}
          </div>
        )}
        <div className={cn("px-6 pb-5 pt-3 text-body text-label-2", footer && "min-h-0 flex-1 overflow-y-auto")}>
          {children}
        </div>
        {footer && (
          <div className="flex shrink-0 items-center justify-end gap-2 border-t border-separator px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
