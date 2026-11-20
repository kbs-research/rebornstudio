/**
 * 입력란 공통 클래스. Input·Select·검색창·여러 줄 입력란이 같은 규격을 쓴다.
 *
 * 기본은 흰 면(`bg-panel`)과 조작할 수 있는 요소의 테두리(`border-separator-strong`)다.
 * hover에서는 테두리만 진해진다. 면 색을 바꾸면 누르는 요소로 읽힌다.
 * focus는 호박색 테두리와 옅은 링이다. 링을 두껍게 하면 좁은 입력란에서 글자를 덮는다.
 * `aria-invalid="true"`를 달면 테두리가 위험색이 되고, 오류 문장은 `FormError`가 입력란 아래에 그린다.
 * 비활성은 가라앉은 면과 흐린 글자다.
 */
export const FIELD_BASE =
  "w-full rounded-field border border-separator-strong bg-panel text-callout text-label " +
  "placeholder:text-placeholder transition-colors " +
  "hover:border-label-4 " +
  "focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus/25 " +
  "aria-[invalid=true]:border-bad aria-[invalid=true]:focus:ring-bad/20 " +
  "disabled:cursor-not-allowed disabled:bg-canvas-2 disabled:text-label-3 disabled:hover:border-separator-strong";

/** 입력란 기본 높이 38px. 조밀한 곳(툴바)은 호출부가 덮어쓴다. */
export const FIELD_H = "h-9.5";
