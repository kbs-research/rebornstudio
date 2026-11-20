/**
 * mutation 실패를 토스트 두 줄(결과, 안내)로 알리는 도우미. 멤버·프로젝트 설정·메타 일괄 입력 화면이 함께 쓴다.
 */
import type { useToast } from "@/shared/ui/Toast";
import type { ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";

/** 토스트를 띄우는 함수의 타입. `useToast().toast`와 같다. */
export type ToastFn = ReturnType<typeof useToast>["toast"];

/**
 * mutation 실패를 토스트 두 줄(결과, 안내)로 알린다.
 *
 * 서버가 사유를 주면 그 사유를 보여 준다. 사유 사전(`shared/api/error_text`)에 이 영역의 오류 코드가 모두 있다.
 * 사전에 없는 실패(네트워크·5xx)에만 다시 시도를 권한다.
 * 원인을 단정하는 문구, 다시 해도 안 되는 실패에 "다시 시도"를 권하는 문구, 안내가 빠진 고정 문구는 쓰지 않는다.
 */
export function failToast(toast: ToastFn, e: unknown, fallback: string) {
  const { message, hint } = errorText((e as Partial<ApiError>)?.detail, {
    message: fallback,
    hint: "잠시 후 다시 시도해주세요.",
  });
  toast("error", message, hint);
}
