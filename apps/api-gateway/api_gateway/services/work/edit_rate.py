"""수정률의 재료인 AI 초안 대비 라벨러 수정 글자 수 측정.

수정률은 "AI 초안 대비 작업자 수정 문자 수 비율"이다. 비율을 내려면 서버에 두 값이 있어야 한다.
라벨러가 받은 AI 초안은 화면에만 있으므로 서버가 같은 출처에서 직접 읽는다.
`labels.diff_chars_count`는 직전 제출과의 길이 차라서 편집량으로 쓸 수 없다.
그래서 이 모듈이 초안과 편집 거리를 정하고,
제출할 때 한 번 재서 라벨 행에 남긴다(`work.service.submit`).
집계는 남은 수를 더하기만 한다(`analytics.dashboard._labeler_counts`).
규칙을 바꿔도 이미 잰 행은 그 시점의 규칙으로 남고, 라벨 행의 `ai_draft`로 다시 잴 수 있다.

AI 초안은 `clips.auto_caption["summary"]`에서 앞뒤 공백을 뗀 값이다. 워크벤치가 캡션에 채우는
`ClipDTO.scene_summary`의 출처와 같다(`media.clip._to_dto`). 화면이 보낸 초안을 쓰면
제출본과 같은 문자열을 초안으로 보내 수정률을 0으로 만들 수 있다.
장면 요약이 없는 클립은 재지 않는다.

재는 대상은 작업(work_item)의 첫 제출(`parent_label_id IS NULL`)뿐이다.
반려 뒤 재제출이나 다른 라벨러가 넘겨받은 작업의 제출은 앞선 제출에서 시작하므로,
다시 재면 같은 교정이 두 번 세어지거나 앞사람이 고친 글자가 뒷사람 몫이 된다.
검수자의 수정 후 승인은 이 경로를 거치지 않는다(`review.decide`).

두 문자열은 같은 형태로 정규화한다.
캡션 문자열은 `caption.final_text`가 정하고, 납품이 읽는 캡션과 같은 값이다.
한글이 자모로 풀린 채(NFD) 들어오면 음절 하나가 여러 글자로 세어지므로 NFC로 합친다.
워크벤치는 초안을 문장으로 나눴다가 공백으로 다시 이어 줄바꿈이 공백이 된다. 그래서 연속 공백을
한 칸으로 합치고 앞뒤 공백을 뗀다. 띄어쓰기 교정(한 칸 넣기·빼기)은 그대로 한 글자로 센다.

글자 단위는 유니코드 코드 포인트다. 편집 거리는 레벤슈타인 거리다(넣기·빼기·바꾸기 각 1).
한 건의 수정률은 `편집 거리 ÷ 초안 글자 수`이고, 초안보다 길게 덧붙이면 100%를 넘을 수 있다.
분모를 긴 쪽으로 두면 덧붙인 양이 수정률에 드러나지 않으므로 초안에 둔다.
"""
import unicodedata
from dataclasses import dataclass

from api_gateway.services import caption


@dataclass(frozen=True)
class EditMeasure:
    """제출 한 건의 측정값. 라벨 행의 세 열(`ai_draft`·`edit_distance`·`draft_chars`)이 된다."""

    #: 라벨러가 받은 AI 초안 원문. 앞뒤 공백만 뗀다.
    #: 화면은 이 원문을 개정 체인의 첫 단으로 그대로 표시한다.
    draft: str
    #: 정규화한 초안과 정규화한 제출 캡션 사이의 편집 거리.
    distance: int
    #: 정규화한 초안의 글자 수. 수정률의 분모다. 빈 초안은 재지 않으므로 항상 1 이상이다.
    draft_chars: int


def ai_draft_of(auto_caption: dict | None) -> str | None:
    """라벨러가 워크벤치에서 초안으로 받는 문자열을 돌려준다. 없으면 `None`을 돌려준다.

    `media.clip._to_dto`의 `scene_summary`와 같은 필드를 읽고, 워크벤치와 같이 앞뒤 공백을 뗀다.
    값이 문자열이 아니거나 비어 있으면 초안이 없는 경우로 본다.
    """
    if not isinstance(auto_caption, dict):
        return None
    summary = auto_caption.get("summary")
    if not isinstance(summary, str):
        return None
    return summary.strip() or None


def caption_text_of(payload: dict) -> str:
    """제출 payload의 캡션 문자열을 돌려준다. 기준 구현은 `caption.final_text` 하나다.

    기준 구현은 삭제 표시를 뺀 문장을 잇는다. 여기서 `brief`를 먼저 읽으면
    납품되지 않을 글자까지 수정량에 들어간다.
    """
    return caption.final_text(payload)


def normalize(text: str) -> str:
    """비교할 형태로 정규화한다. NFC로 합치고 연속 공백을 한 칸으로 줄이며 앞뒤 공백을 뗀다."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def edit_distance(a: str, b: str) -> int:
    """글자 단위 레벤슈타인 거리를 계산한다. 넣기·빼기·바꾸기를 각 1로 센다.

    캡션은 5,000자까지 올 수 있고 이 계산은 제출 요청 안에서 실행된다.
    표를 채우는 O(n·m) 방식은 순수 파이썬에서 그 길이면 초 단위가 걸리므로
    비트 병렬 알고리즘(Myers 1999, Hyyrö 2001)을 쓴다. 짧은 쪽 길이의 정수 하나로 표의 한 열을
    한 번에 갱신하므로 긴 쪽 길이만큼만 반복한다. 공통 접두·접미는 먼저 떼어 낸다.

    Args:
        a: 한쪽 문자열. 정규화는 호출하는 쪽이 한다(`measure`).
        b: 다른 쪽 문자열.

    Returns:
        0 이상의 정수. 두 문자열이 같으면 0, 한쪽이 비면 다른 쪽 길이다.
    """
    # 공통 접두·접미는 거리에 영향이 없으므로 먼저 뗀다.
    head = 0
    limit = min(len(a), len(b))
    while head < limit and a[head] == b[head]:
        head += 1
    end_a, end_b = len(a), len(b)
    while end_a > head and end_b > head and a[end_a - 1] == b[end_b - 1]:
        end_a -= 1
        end_b -= 1
    a, b = a[head:end_a], b[head:end_b]
    if len(a) < len(b):
        a, b = b, a  # 짧은 쪽을 b에 둔다. b의 길이가 비트 벡터의 폭이다
    m = len(b)
    if m == 0:
        return len(a)

    # 글자마다 b의 어느 위치에 그 글자가 있는지 비트마스크로 만든다.
    peq: dict[str, int] = {}
    for i, ch in enumerate(b):
        peq[ch] = peq.get(ch, 0) | (1 << i)
    mask = (1 << m) - 1
    high = 1 << (m - 1)
    # 세로 차이(+1/−1) 벡터와 마지막 행 값. 첫 열은 0,1,…,m이라 모두 +1에서 시작한다.
    vp, vn, dist = mask, 0, m
    for ch in a:
        eq = peq.get(ch, 0)
        xv = eq | vn
        xh = (((eq & vp) + vp) ^ vp) | eq
        hp = vn | (~(xh | vp) & mask)
        hn = vp & xh
        if hp & high:
            dist += 1
        elif hn & high:
            dist -= 1
        # 전역 거리라서 첫 행이 열마다 1씩 오른다. 그래서 가로 +1을 밀어 넣는다.
        hp = ((hp << 1) | 1) & mask
        hn = (hn << 1) & mask
        vp = hn | (~(xv | hp) & mask)
        vn = hp & xv
    return dist


def measure(auto_caption: dict | None, payload: dict) -> EditMeasure | None:
    """제출 한 건의 수정량을 잰다. 초안이 없으면 재지 않고 `None`을 돌려준다(0과 구분한다).

    Args:
        auto_caption: 제출하는 클립의 `clips.auto_caption`. 초안은 여기서만 읽는다.
        payload: 제출 payload(`WorkSubmitRequest.payload`).

    Returns:
        초안, 편집 거리, 초안 글자 수. 초안이 없거나 비어 있으면 `None`이다.
    """
    draft = ai_draft_of(auto_caption)
    if draft is None:
        return None
    base = normalize(draft)
    if not base:
        return None
    return EditMeasure(
        draft=draft,
        distance=edit_distance(base, normalize(caption_text_of(payload))),
        draft_chars=len(base),
    )
