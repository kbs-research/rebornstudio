/** 프로젝트를 찾지 못했을 때의 셸. 개요·통계·콘텐츠 화면이 함께 쓴다. */
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";

// 페이지 셸 공용 부품. 조회 실패와 "없음"을 구분한다.

interface Props {
  /** 프로젝트 목록 조회가 실패했으면 true. "없음" 대신 실패를 그린다. */
  isError: boolean;
  /**
   * 재시도 함수(선택). 목록을 직접 조회하지 않고 넘겨받기만 하는 호출부는 줄 수 없다.
   * 줄 수 있으면 준다(일시 오류를 영구 실패로 만들지 않는다).
   */
  onRetry?: () => void;
  /**
   * 패널 안에 놓으면 true. 여백·칩·글자 크기를 한 단계 줄인다
   * (`EmptyState`의 `dense`, `ErrorState`의 `inline`과 같은 기준).
   *
   * 기본값은 false다. 화면 전체를 대신하는 조기 반환에서는 크게 그려야 "이 페이지가 비었다"로 읽힌다.
   */
  dense?: boolean;
}

/**
 * 프로젝트를 찾지 못했을 때의 셸을 그린다. 조회 실패와 없음을 구분한다.
 *
 * `onRetry`·`dense`는 선택 인자다. 재시도 함수를 줄 수 없는 호출부와 패널 안에 놓는 호출부도 같은 셸을 쓴다.
 */
export function ProjectShellState({ isError, onRetry, dense = false }: Props) {
  return isError ? (
    <ErrorState
      inline={dense}
      message="프로젝트 조회 실패"
      description="잠시 후 다시 시도해주세요."
      onRetry={onRetry}
    />
  ) : (
    <EmptyState
      dense={dense}
      message="프로젝트 없음"
      description="주소를 확인해주세요. 참여하지 않은 프로젝트는 열 수 없습니다."
    />
  );
}
