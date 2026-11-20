/**
 * 데이터 로드 실패를 보여 주는 공용 컴포넌트. 재시도 버튼을 포함한다.
 *
 * 본문 전체가 실패하면 기본형(가운데 정렬 패널)을, 섹션 안의 한 줄 슬롯에서는 `inline`을 쓴다.
 * 인라인 위치에 실패 문구를 손으로 그리면 재시도 버튼이 빠지기 쉽다.
 *
 * `onRetry`는 가능하면 넘겨서 일시 오류(429·5xx·타임아웃)에서 다시 시도할 수 있게 한다.
 * `EmptyState`와 짝을 이룬다.
 */
import { TriangleAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { apiErrorText, isRetryable } from "@/shared/api/error_text";

interface Props {
  message?: string;
  description?: string;
  onRetry?: () => void;
  /** 섹션 안의 한 줄 슬롯이면 true. 큰 아이콘과 세로 여백을 뺀다. */
  inline?: boolean;
  /**
   * 실패 객체(`ApiError`). 넘기면 다시 해도 결과가 같은 실패(403·404·409)에는
   * 재시도 버튼 대신 사유를 보여 준다.
   * 재시도 가능 여부는 `isRetryable` 한 곳에서 판정한다. queryClient의 재시도 판정과 같다.
   */
  error?: unknown;
}

/**
 * 로드 실패 상태를 그린다. 영구 실패면 재시도 버튼 대신 사유를 보여 준다.
 *
 * 기본 머리글은 이름 위치라서 문장(`문제가 발생했습니다`) 대신 명사구로 쓴다.
 * 호출부가 `message`를 빠뜨려도 기본값이 명사구 규칙을 지킨다.
 */
export function ErrorState({ message = "불러오기 실패", description, onRetry: retry, inline, error }: Props) {
  const permanent = error !== undefined && !isRetryable((error as { status?: number } | null)?.status);
  const onRetry = permanent ? undefined : retry;
  if (permanent && !description) {
    const t = apiErrorText(error);
    description = t.hint ? `${t.message}. ${t.hint}` : t.message;
  }
  if (inline) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <p className="text-callout text-bad-text">{message}</p>
        {description && <p className="text-footnote text-label-3">{description}</p>}
        {onRetry && (
          <Button variant="ghost" size="sm" onClick={onRetry}>
            다시 시도
          </Button>
        )}
      </div>
    );
  }
  // 아이콘 칩은 `EmptyState`와 같은 모양이고 아이콘만 실패 색이다. 머리글은 먹색이다.
  return (
    <div role="alert" className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <div className="mb-1 flex size-10 items-center justify-center rounded-card bg-canvas-2 text-bad-text" aria-hidden>
        <TriangleAlert size={18} />
      </div>
      <p className="text-headline font-semibold text-label">{message}</p>
      {description && <p className="text-callout text-label-2">{description}</p>}
      {onRetry && (
        <Button size="sm" className="mt-2" onClick={onRetry}>
          다시 시도
        </Button>
      )}
    </div>
  );
}
