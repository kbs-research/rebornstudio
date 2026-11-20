/**
 * 폼 필드의 검증 실패를 화면에 보여 준다. `react-hook-form`을 쓰는 화면이 함께 쓴다.
 *
 * `handleSubmit`은 `required`·`minLength` 검증에 걸리면 알림 없이 제출을 막는다.
 * `formState.errors`를 그리지 않으면 요청도 안내도 없어서, 사용자에게는 버튼이 동작하지 않는 것처럼 보인다.
 *
 * `password_reset`은 `required`를 DOM에 그대로 걸어서 브라우저가 안내를 보여 준다.
 * 이 컴포넌트는 브라우저 안내가 나오지 않는 화면에 쓴다.
 *
 * 문구는 이 파일 한 곳에 둔다. 세 화면이 각자 문장을 쓰면 같은 실패가 다른 문구로 나온다.
 */
import { type FieldError } from "react-hook-form";

/**
 * RHF 오류를 사람이 읽는 한 줄로 바꾼다. 규칙에 문구가 있으면 그 문구를, 없으면 종류별 문구를 쓴다.
 * 문구가 정해지지 않은 종류면 값을 확인해 달라고 안내해서 빈 문구가 남지 않게 한다.
 */
export function fieldErrorText(error: FieldError | undefined, minLength?: number): string | null {
  if (!error) return null;
  // 규칙에 문구를 준 경우(`validate`·`maxLength`의 `message`)는 그 문구를 쓴다. 규칙을 정한 쪽의 문구가 사유를 가장 정확히 담는다.
  if (typeof error.message === "string" && error.message) return error.message;
  switch (error.type) {
    case "required":
      return "입력해주세요.";
    case "minLength":
      return minLength ? `${minLength}자 이상으로 입력해주세요.` : "더 길게 입력해주세요.";
    case "pattern":
      return "형식을 확인해주세요.";
    default:
      return "값을 확인해주세요.";
  }
}

/** 입력란 바로 아래에 오류를 한 줄로 보여 준다. 오류가 없으면 아무것도 그리지 않는다. */
export function FormError({ error, minLength }: { error?: FieldError; minLength?: number }) {
  const text = fieldErrorText(error, minLength);
  if (!text) return null;
  return (
    <p role="alert" className="text-caption text-bad-text">
      {text}
    </p>
  );
}
