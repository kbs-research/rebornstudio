/**
 * 교정 diff. 어절 단위로 정렬하고, 바뀐 어절 안은 글자 단위로 좁힌다.
 *
 * 라벨링 워크벤치(VLM 시드 대비·revision 비교)와 검수 화면(문장 수정·전체 편집)이 함께 쓴다.
 *
 * 영어는 낱말이 짧고 공백이 의미 단위와 거의 같아(`the`를 `a`로) 어절 단위 비교로 충분하다.
 * 한국어는 교착어라 어절 하나에 어간·어미·조사가 모두 붙어 길다.
 * 그래서 어절 단위로만 비교하면 한 글자만 고쳐도 어절 전체가 빨강과 초록으로 표시되고, 바뀐 글자를 가리키지 못한다.
 *
 *     `소환했습니다.` → `소환했다.`      (바뀐 글자는 `습니`뿐인데 어절 전체가 표시된다)
 *     `무함마드가`    → `무함마드는`      (조사 한 글자인데 이름까지 표시된다)
 *
 * 좁히는 방법: 어절 LCS로 정렬한 다음, 서로 바뀐 어절 쌍(삭제 n개 ↔ 추가 n개)을 짝지어
 * 그 안에서 글자 LCS를 한 번 더 돌린다(git `--word-diff`와 같은 방식).
 * 개수가 다르면 덩어리째 이어 붙여 비교한다(`refine` 참고).
 * 두 어절의 유사도가 `MIN_SIMILARITY` 미만이면 좁히지 않는다.
 * 서로 무관한 두 낱말을 글자로 나누면 색이 여기저기 흩어져 오히려 읽기 어렵다.
 *
 * 좁힌 조각은 한 어절 안이라 조각 사이에 공백을 넣지 않도록 `tight`를 붙인다
 * (렌더는 `DiffText` 한 곳에서 한다).
 */
export type DiffOp = {
  type: "eq" | "add" | "del";
  text: string;
  /** 같은 어절 안에서 나눈 조각이면 true. 뒤에 공백을 넣지 않는다. */
  tight?: boolean;
};

/** 글자 DP를 돌리는 어절의 최대 길이. 글자 DP는 O(n·m)이라 아주 긴 어절(붙여쓴 URL·해시 등)에서는 돌리지 않는다. */
const MAX_REFINE_LEN = 200;
/** 글자로 좁히는 최소 유사도. 이보다 낮으면 통째 교체로 둔다. 닮지 않은 어절을 글자로 나누면 읽기 어렵다. */
const MIN_SIMILARITY = 0.4;

/** 토큰 배열 두 개의 LCS diff를 구한다. 어절과 글자에 같은 함수를 쓴다. */
function diffTokens(a: string[], b: string[]): DiffOp[] {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const ops: DiffOp[] = [];
  let i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && a[i] === b[j]) {
      ops.push({ type: "eq", text: a[i] }); i++; j++;
    } else if (i < m && (j >= n || dp[i + 1][j] >= dp[i][j + 1])) {
      ops.push({ type: "del", text: a[i] }); i++;
    } else {
      ops.push({ type: "add", text: b[j] }); j++;
    }
  }
  return ops;
}

/**
 * 서로 바뀐 어절 한 쌍을 글자 단위로 좁힌다. 닮지 않았으면 `null`을 돌려준다(통째로 두라는 뜻).
 *
 * `Array.from`으로 나눈다. `split("")`은 이모지·일부 한자를 반으로 자른다.
 */
function refinePair(delText: string, addText: string): DiffOp[] | null {
  const a = Array.from(delText), b = Array.from(addText);
  if (!a.length || !b.length) return null;
  if (a.length > MAX_REFINE_LEN || b.length > MAX_REFINE_LEN) return null;

  const raw = diffTokens(a, b);
  const same = raw.reduce((n, o) => n + (o.type === "eq" ? Array.from(o.text).length : 0), 0);
  if (same / Math.max(a.length, b.length) < MIN_SIMILARITY) return null;

  // 같은 종류가 이어지면 한 조각으로 합친다. 글자마다 상자를 그리면 읽을 수 없다.
  const merged: DiffOp[] = [];
  for (const op of raw) {
    const last = merged[merged.length - 1];
    if (last && last.type === op.type) last.text += op.text;
    else merged.push({ ...op });
  }
  // 한 어절 안이므로 마지막을 뺀 전부를 붙여 쓴다.
  return merged.map((op, k) => (k < merged.length - 1 ? { ...op, tight: true } : op));
}

/**
 * 바뀐 덩어리마다 글자 단위로 좁힌다.
 *
 * 어절 개수가 같으면 1:1로 짝지어 각각 좁힌다(`the`를 `a`로 바꾸는 낱말 교체).
 *
 * 개수가 다르면 덩어리를 통째로 이어 붙여 좁힌다.
 * 한국어는 어미가 바뀌면 어절 수도 함께 바뀌는 일이 잦다
 * (`진행하는 듯합니다.` → `진행했습니다.`, 2어절이 1어절로 바뀐다).
 * 1:1로만 짝지으면 이런 흔한 편집이 모두 어절 통째 교체로 남는다.
 * 이어 붙인 뒤에도 닮지 않았으면 `refinePair`가 `null`을 주므로 어절 단위로 돌아간다.
 * 그래서 서로 무관한 문장은 여전히 어절 단위로 표시된다.
 */
function refine(ops: DiffOp[]): DiffOp[] {
  const out: DiffOp[] = [];
  let i = 0;
  while (i < ops.length) {
    if (ops[i].type === "eq") { out.push(ops[i]); i++; continue; }
    let j = i;
    while (j < ops.length && ops[j].type !== "eq") j++;
    const block = ops.slice(i, j);
    const dels = block.filter((o) => o.type === "del");
    const adds = block.filter((o) => o.type === "add");
    if (dels.length > 0 && adds.length > 0) {
      if (dels.length === adds.length) {
        for (let k = 0; k < dels.length; k++) {
          const refined = refinePair(dels[k].text, adds[k].text);
          if (refined) out.push(...refined);
          else out.push(dels[k], adds[k]);
        }
      } else {
        // 어절 수가 달라진 편집. 덩어리째 이어 붙여 비교한다(공백도 글자로 함께 비교된다).
        const refined = refinePair(
          dels.map((o) => o.text).join(" "),
          adds.map((o) => o.text).join(" "),
        );
        if (refined) out.push(...refined);
        else out.push(...block);
      }
    } else {
      out.push(...block);   // 순수 삽입·순수 삭제는 짝지을 상대가 없어 그대로 둔다.
    }
    i = j;
  }
  return out;
}

/** 두 문자열의 교정 diff를 구한다. 어절로 맞추고 바뀐 어절 안은 글자로 좁힌다. */
export function wordDiff(a: string, b: string): DiffOp[] {
  return refine(diffTokens(a.split(/\s+/).filter(Boolean), b.split(/\s+/).filter(Boolean)));
}

/**
 * 고친 곳의 수를 센다. 0이면 diff를 그리지 않는다.
 *
 * 이어진 add·del 한 덩어리를 한 곳으로 센다.
 * 낱말 하나를 바꾸면 `del`과 `add` 두 조각이 나오므로, 조각 수를 세면 고친 곳 하나가 "2곳"이 된다.
 * 글자 단위로 좁히면 조각이 더 잘게 나뉘어 조각 수가 더 부풀려진다.
 *
 * `wordDiff`는 공백으로 나눠 비교하므로 줄바꿈·연속 공백만 다른 두 문자열은 모두 `eq`이고 결과는 0이다.
 * 문장 배열을 `" "`로 다시 이으면 원본과 문자열이 달라진다.
 * 그래서 `a !== b`로 판단하면 고친 것이 없는데도 교정 패널이 떠서 같은 캡션을 두 번 읽게 한다.
 * 판단은 문자열 비교 대신 diff 연산으로 한다.
 */
export function countChanges(ops: DiffOp[]): number {
  let sites = 0;
  for (let i = 0; i < ops.length; i++) {
    if (ops[i].type !== "eq" && (i === 0 || ops[i - 1].type === "eq")) sites++;
  }
  return sites;
}
