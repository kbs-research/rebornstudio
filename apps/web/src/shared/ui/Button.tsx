/** 공용 버튼과 버튼 표면 클래스. */
import { ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "@/shared/lib/cn";

/**
 * 버튼 변형.
 *
 * - `primary`: 먹색 채움. 한 화면의 주 동작 하나에 쓴다.
 * - `secondary`: 흰 면과 테두리. 취소·보조 동작에 쓴다.
 * - `ghost`: 면 없는 글자 버튼. 닫기·접기처럼 가벼운 동작에 쓴다.
 * - `danger-outline`: 위험 테두리. 페이지 안에 놓이는 비가역 동작(삭제·폐기)의 첫 버튼이다.
 * - `danger`: 위험 채움. 비가역 동작을 확인하는 창의 마지막 버튼이다.
 * - `submit`: 호박색 채움. 워크벤치의 제출 하나에만 쓴다.
 */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-outline" | "submit";
type Size = "sm" | "md" | "lg";

/**
 * 변형별 면·글자·테두리 클래스.
 * 비활성은 투명도 대신 가라앉은 면과 옅은 글자로 그린다. 투명도로 흐리면 흑연색 면에서 글자가 보이지 않는다.
 */
const variantClasses: Record<Variant, string> = {
  primary:
    "bg-accent-fill font-semibold text-accent-fg hover:bg-accent-fill-hover " +
    "disabled:bg-canvas-2 disabled:text-label-4",
  secondary:
    "border border-separator-strong bg-panel font-medium text-label hover:bg-canvas-2 " +
    "disabled:text-label-4 disabled:hover:bg-panel",
  ghost: "bg-transparent font-medium text-label-2 hover:bg-fill hover:text-label disabled:text-label-4 disabled:hover:bg-transparent",
  "danger-outline":
    "border border-bad bg-panel font-semibold text-bad-text hover:bg-bad-muted " +
    "disabled:border-separator disabled:text-label-4 disabled:hover:bg-panel",
  danger:
    "bg-bad font-semibold text-bad-fg hover:brightness-95 disabled:bg-canvas-2 disabled:text-label-4 disabled:hover:brightness-100",
  submit:
    "bg-amber font-bold text-on-amber hover:bg-amber-strong disabled:bg-canvas-2 disabled:text-label-4",
};
/** 크기. md가 기본 36px다. sm은 표 머리·툴바, lg는 인증 화면처럼 폼 하나가 전부인 곳에 쓴다. */
const sizeClasses: Record<Size, string> = {
  sm: "h-8 px-3 text-footnote",
  md: "h-9 px-4 text-footnote",
  lg: "h-10 px-5 text-callout",
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

/* 포커스 표시는 전역 기본값(`globals.css`의 2px 띄운 호박색 외곽선)을 쓴다. 띄운 틈에 실제 바탕이 보인다. */
const baseClasses =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control transition-colors " +
  "disabled:cursor-not-allowed";

/**
 * 버튼의 표면 클래스만 돌려준다. `<Link>`처럼 `<button>`이 아닌 요소에 쓴다.
 *
 * 주 동작이 페이지 이동이면 `<Link className={buttonClasses(...)}>`로 그린다.
 * 클래스를 손으로 옮겨 적으면 변형이 바뀔 때 그 버튼만 이전 모양으로 남는다.
 * 주의: `<Link>`를 `<Button>`으로 감싸지 않는다. 링크 안에 버튼이 들어가면 접근성이 깨진다.
 */
export function buttonClasses(variant: Variant = "primary", size: Size = "md", className?: string) {
  return cn(baseClasses, variantClasses[variant], sizeClasses[size], className);
}

/**
 * 공용 버튼을 그린다. `type`을 주지 않으면 `button`으로 렌더한다.
 *
 * HTML 기본값(`submit`)을 그대로 두면 폼 안에서 `type` 없는 버튼이 모두 제출 버튼이 되어,
 * 모달을 `<form>`으로 감쌌을 때 취소·닫기가 저장을 부른다. 제출 버튼은 `type="submit"`을 적어서 만든다.
 */
export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ className, variant = "primary", size = "md", type = "button", ...rest }, ref) => (
    <button ref={ref} type={type} className={buttonClasses(variant, size, className)} {...rest} />
  ),
);
Button.displayName = "Button";
