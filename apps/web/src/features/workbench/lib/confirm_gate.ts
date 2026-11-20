/**
 * 확인 창의 실행을 한 번만 허용하는 잠금.
 *
 * 실행 버튼의 `disabled`는 `pending`이 다음 렌더에 반영된 뒤에야 걸린다. TanStack Query가 상태 변경을
 * 다음 틱에 알리므로 짧은 간격의 더블클릭은 두 번째 클릭도 활성 버튼을 눌러 요청이 두 번 나간다.
 * 그러면 두 번째 요청이 409를 받아 성공한 제출 뒤에 "이미 처리된 작업" 오류가 뜬다.
 * 같은 변이의 마지막 호출만 콜백을 받으므로 성공한 요청의 화면 이동도 실행되지 않는다.
 * 그래서 렌더를 기다리지 않는 값으로 막고, 렌더 없이 테스트하려고 컴포넌트 밖에 둔다.
 */

/** 실행 잠금. 한 번 잠그면 풀 때까지 다시 잠글 수 없다. */
export interface ConfirmGate {
  /** 잠겨 있지 않으면 잠그고 true, 잠겨 있으면 false를 돌려준다. */
  tryEnter(): boolean;
  /** 잠금을 푼다. 실행이 끝났거나(실패 포함) 창이 닫히거나 열릴 때 부른다. */
  release(): void;
}

/** 풀린 상태의 실행 잠금을 만든다. */
export function createConfirmGate(): ConfirmGate {
  let locked = false;
  return {
    tryEnter() {
      if (locked) return false;
      locked = true;
      return true;
    },
    release() {
      locked = false;
    },
  };
}
