/**
 * 낱말 단위 diff를 본문 안에 그리는 공용 컴포넌트.
 *
 * 그리는 코드를 화면마다 두면 모서리·색 같은 수정을 여러 번 해야 하므로 한 곳에서 그린다.
 *
 * 강조는 대화형이 아닌 텍스트 라벨이라, 텍스트 라벨은 capsule을 쓴다는 규칙에 따라 모서리를 capsule로 둔다.
 * 맨 `rounded`(`0.25rem`)는 루트 글자 크기에 따라 바뀌므로 쓰지 않는다.
 * 본문 높이에서는 `card`(10px)든 capsule이든 CSS가 상자 높이의 절반으로 잘라 비슷하게 그리지만,
 * 라운드 이름을 하나로 맞추려고 규칙을 따른다.
 *
 * 낱말 뒤 공백은 강조 밖에 둔다.
 * `{op.text}{" "}`처럼 공백까지 색칠하면 강조가 공백 한 글자만큼 길어져 다음 낱말에 붙어 보인다.
 *
 * 글자 크기는 `className`으로 받는다. 부르는 곳마다 문맥 크기가 달라서(워크벤치는 압축, 검수 화면은 본문)
 * 이 컴포넌트에서 크기를 고정하면 부모가 정한 크기를 덮어쓴다.
 */
import { Fragment } from "react";

import { cn } from "@/shared/lib/cn";
import type { DiffOp } from "@/shared/lib/diff";

interface Props {
  ops: DiffOp[];
  /** 감싸는 span의 클래스. 글자 크기와 행간은 부르는 쪽이 정한다. */
  className?: string;
}

/** diff 조각을 차례로 그린다. 추가는 ok 색, 삭제는 bad 색과 취소선으로 표시한다. */
export function DiffText({ ops, className }: Props) {
  return (
    <span className={className}>
      {ops.map((op, i) => (
        <Fragment key={i}>
          {op.type === "eq" ? (
            <span>{op.text}</span>
          ) : (
            <span
              className={cn(
                "rounded-capsule px-0.5",
                op.type === "add"
                  ? "bg-ok-muted text-ok-text"
                  : "bg-bad-muted text-bad-text line-through",
              )}
            >
              {op.text}
            </span>
          )}
          {/* 어절 안에서 좁힌 조각(`tight`)은 붙여 쓴다. 공백을 넣으면 `있습니다.ㅇㄹ`이
              `있습니다. ㅇㄹ`로 나뉘어 원문에 없는 띄어쓰기가 생긴다. */}
          {op.tight ? null : " "}
        </Fragment>
      ))}
    </span>
  );
}
