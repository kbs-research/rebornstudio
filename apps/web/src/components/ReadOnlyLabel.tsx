/** 제출된 라벨 데이터의 읽기 전용 보기. */
import type { ReactNode } from "react";

import {
  liveSentences,
  sentencesOf,
  CaptionBody,
} from "@/features/workbench";

/**
 * 제출된 label_data의 캡션을 읽기 전용으로 렌더한다.
 *
 * 검수 페이지(review)와 내 라벨링 기록 상세(inbox 드로어)가 함께 쓴다.
 * `fontSize`는 캡션 글자 크기(기본 15px), `captionSlot`은 캡션 위치에 대신 그릴 노드(검수의 문장별 트리아지)다.
 *
 * 라벨러가 채우는 내용은 캡션 하나다. 이전 데이터에 남은 `description`·`keywords`·`tags`는
 * 입력란이 없어 그리지 않는다. 라벨 데이터 안의 이전 `meta`도 납품에 실리지 않는 값이라 그리지 않는다.
 */
export function ReadOnlyLabel({
  data,
  fontSize = 15,
  captionSlot,
  captionTitle = true,
}: {
  data: unknown;
  fontSize?: number;
  captionSlot?: ReactNode;
  /** 캡션 절 이름을 그리면 true. 검수 워크벤치는 절 머리를 직접 그린다. */
  captionTitle?: boolean;
}) {
  /* 캡션은 `sentencesOf` 하나로 읽는다. 문장 배열·`brief` 문자열·`caption` 문자열을 모두 읽는다.
     `caption.brief`만 읽으면 문장 배열만 있는 제출을 "없음"으로 그린다. */
  const captionSentences = liveSentences(sentencesOf(data)).map((s) => s.text);

  return (
    <section className="text-footnote">
      {captionTitle && <h4 className="mb-2 text-caption font-semibold text-label-2">캡션</h4>}
      {captionSlot ? (
        captionSlot
      ) : captionSentences.length > 0 ? (
        <CaptionBody sentences={captionSentences} fontSize={fontSize} />
      ) : (
        // `— 없음`으로 쓰면 대시가 값 위치의 `—`와 겹쳐 기호가 두 개가 된다.
        <p className="text-caption text-label-3">없음</p>
      )}
    </section>
  );
}
